import { useEffect, useRef } from 'react';

/**
 * GalaxyBackground — a canvas-based animated starfield that mimics a Three.js
 * particle system without the library weight. Features:
 *  - 3D-projected stars with depth (parallax drift + perspective twinkle)
 *  - slow rotating galactic disk (denser band across the middle)
 *  - drifting nebula clouds (radial gradients)
 *  - occasional shooting stars
 *  - subtle parallax following the mouse
 *
 * Light-theme tuned: stars are soft ink/jewel dots on a warm paper canvas.
 */

interface Star3D {
  x: number;       // normalized -1..1 position on galactic plane
  y: number;       // normalized -1..1
  z: number;       // depth 0..1 (0 = far, 1 = near)
  baseSize: number;
  hue: number;     // color variant
  twinklePhase: number;
  twinkleSpeed: number;
}

interface Shooting {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  maxLife: number;
}

const STAR_COLORS = [
  [66, 99, 235],    // royal blue
  [156, 54, 181],   // plum
  [230, 119, 0],    // ochre
  [47, 158, 68],    // sage
  [30, 26, 60],     // ink
  [92, 124, 250],   // soft blue
  [214, 51, 108],   // rose
];

const NEBULA_COLORS = [
  [92, 124, 250, 0.020],   // blue
  [174, 62, 201, 0.016],   // plum
  [230, 119, 0, 0.012],    // ochre
];

export default function GalaxyBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef({ x: 0, y: 0, tx: 0, ty: 0 });

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const canvas: HTMLCanvasElement = el;
    const c = canvas.getContext('2d', { alpha: true });
    if (!c) return;
    const ctx: CanvasRenderingContext2D = c;

    let w = 0;
    let h = 0;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    let stars: Star3D[] = [];
    let shooting: Shooting[] = [];
    let rotation = 0;
    let rafId = 0;
    let lastShoot = 0;

    function resize() {
      w = window.innerWidth;
      h = window.innerHeight;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = w + 'px';
      canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      initStars();
    }

    function initStars() {
      // density scales with screen area, capped for perf — kept sparse so it
      // reads as a subtle texture behind content, not a distraction
      const count = Math.min(220, Math.floor((w * h) / 6500));
      stars = [];
      for (let i = 0; i < count; i++) {
        // galactic disk distribution: concentrate stars near y=0 plane
        const angle = Math.random() * Math.PI * 2;
        const radius = Math.pow(Math.random(), 0.6); // bias toward center
        const diskNoise = (Math.random() - 0.5) * 0.5 * (1 - radius * 0.5);
        stars.push({
          x: Math.cos(angle) * radius + (Math.random() - 0.5) * 0.3,
          y: diskNoise + (Math.random() - 0.5) * 0.8,
          z: Math.random(),
          baseSize: 0.4 + Math.random() * 1.3,
          hue: Math.floor(Math.random() * STAR_COLORS.length),
          twinklePhase: Math.random() * Math.PI * 2,
          twinkleSpeed: 0.5 + Math.random() * 1.5,
        });
      }
    }

    function spawnShooting() {
      const fromTop = Math.random() < 0.5;
      const startX = Math.random() * w;
      const startY = fromTop ? -20 : Math.random() * h * 0.5;
      const angle = (Math.PI / 6) + Math.random() * (Math.PI / 4); // 30-75 deg
      const speed = 6 + Math.random() * 5;
      shooting.push({
        x: startX,
        y: startY,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 0,
        maxLife: 60 + Math.random() * 30,
      });
    }

    function drawNebula(time: number) {
      const t = time * 0.00008;
      for (let i = 0; i < NEBULA_COLORS.length; i++) {
        const c = NEBULA_COLORS[i];
        const phase = t + i * 2.1;
        const cx = w * (0.5 + Math.cos(phase) * 0.35);
        const cy = h * (0.5 + Math.sin(phase * 0.8) * 0.3);
        const r = Math.max(w, h) * (0.3 + Math.sin(phase * 1.3) * 0.05);
        const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        grad.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},${c[3]})`);
        grad.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`);
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, w, h);
      }
    }

    function drawStars(time: number) {
      const cx = w / 2;
      const cy = h / 2;
      const scale = Math.max(w, h) * 0.55;
      // parallax offset from mouse (smoothed)
      mouseRef.current.x += (mouseRef.current.tx - mouseRef.current.x) * 0.04;
      mouseRef.current.y += (mouseRef.current.ty - mouseRef.current.y) * 0.04;
      const px = mouseRef.current.x * 30;
      const py = mouseRef.current.y * 30;

      for (let i = 0; i < stars.length; i++) {
        const s = stars[i];
        // rotate around galactic center (z-axis), slower for far stars
        const rotSpeed = 0.02 + s.z * 0.04;
        const cosR = Math.cos(rotation * rotSpeed);
        const sinR = Math.sin(rotation * rotSpeed);
        const rx = s.x * cosR - s.y * sinR;
        const ry = s.x * sinR + s.y * cosR;

        // perspective projection
        const persp = 0.5 + s.z * 0.5;
        const sx = cx + rx * scale * persp + px * s.z;
        const sy = cy + ry * scale * persp + py * s.z;

        // skip off-screen
        if (sx < -10 || sx > w + 10 || sy < -10 || sy > h + 10) continue;

        // twinkle
        const twinkle = 0.5 + 0.5 * Math.sin(time * 0.001 * s.twinkleSpeed + s.twinklePhase);
        const alpha = (0.08 + s.z * 0.20) * (0.4 + twinkle * 0.6);
        const size = s.baseSize * (0.5 + s.z * 0.8) * (0.8 + twinkle * 0.4);

        const col = STAR_COLORS[s.hue];
        // only the nearest, brightest stars get a faint halo — kept very subtle
        if (s.z > 0.85 && size > 1.4) {
          const halo = ctx.createRadialGradient(sx, sy, 0, sx, sy, size * 2.5);
          halo.addColorStop(0, `rgba(${col[0]},${col[1]},${col[2]},${alpha * 0.12})`);
          halo.addColorStop(1, `rgba(${col[0]},${col[1]},${col[2]},0)`);
          ctx.fillStyle = halo;
          ctx.beginPath();
          ctx.arc(sx, sy, size * 2.5, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},${alpha})`;
        ctx.beginPath();
        ctx.arc(sx, sy, size, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function drawShooting() {
      for (let i = shooting.length - 1; i >= 0; i--) {
        const s = shooting[i];
        s.x += s.vx;
        s.y += s.vy;
        s.life++;
        const lifeRatio = s.life / s.maxLife;
        const alpha = lifeRatio < 0.15 ? lifeRatio / 0.15 : 1 - (lifeRatio - 0.15) / 0.85;

        // tail
        const tailLen = 80;
        const grad = ctx.createLinearGradient(s.x, s.y, s.x - s.vx * tailLen / 6, s.y - s.vy * tailLen / 6);
        grad.addColorStop(0, `rgba(66, 99, 235, ${alpha * 0.32})`);
        grad.addColorStop(0.5, `rgba(156, 54, 181, ${alpha * 0.14})`);
        grad.addColorStop(1, 'rgba(66, 99, 235, 0)');
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.2;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.lineTo(s.x - s.vx * tailLen / 6, s.y - s.vy * tailLen / 6);
        ctx.stroke();

        // head
        ctx.fillStyle = `rgba(66, 99, 235, ${alpha * 0.85})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, 1.3, 0, Math.PI * 2);
        ctx.fill();

        if (s.life >= s.maxLife || s.x > w + 100 || s.y > h + 100) {
          shooting.splice(i, 1);
        }
      }
    }

    function frame(time: number) {
      ctx.clearRect(0, 0, w, h);
      drawNebula(time);
      drawStars(time);
      drawShooting();
      rotation += 0.002;

      // occasional shooting star
      if (time - lastShoot > 4000 + Math.random() * 6000) {
        spawnShooting();
        lastShoot = time;
      }

      rafId = requestAnimationFrame(frame);
    }

    function onMouseMove(e: MouseEvent) {
      mouseRef.current.tx = (e.clientX / w - 0.5) * 2;
      mouseRef.current.ty = (e.clientY / h - 0.5) * 2;
    }

    resize();
    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMouseMove);
    rafId = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', onMouseMove);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: -1,
        pointerEvents: 'none',
      }}
    />
  );
}
