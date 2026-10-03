import { CanvasTexture, SRGBColorSpace } from "three";
import type { TextureSpec } from "../data/kb";

/**
 * Procedural canvas "document artwork" for each of the 40 sheets - rules,
 * boxes, redacted-looking filler bars, a kicker/title pair. Recognisable by
 * shape at a glance; nothing drawn here needs to be read, which is what
 * keeps it inside rule 1 (readable text must live in the DOM, never a WebGL
 * texture - the reference's own comment at this point in the file makes the
 * same distinction: "Anything that must be read lives in the DOM card.").
 * Ported from reference-landing.html:688-800.
 *
 * `TextureSpec`'s positional-array fields in the reference (`a[0]`, `a[1]`,
 * ...) are addressed by name here instead - see data/kb.ts's file note.
 */

const TEX_WIDTH = 330;
const TEX_HEIGHT = 450;
/** The reference draws in a fixed 440x600 logical unit space, then scales
 *  that down to the actual canvas pixel size - kept as-is so every hand-
 *  tuned coordinate below matches the reference exactly. */
const UNIT_WIDTH = 440;
const UNIT_HEIGHT = 600;

function paper(): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const canvas = document.createElement("canvas");
  canvas.width = TEX_WIDTH;
  canvas.height = TEX_HEIGHT;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(TEX_WIDTH / UNIT_WIDTH, TEX_HEIGHT / UNIT_HEIGHT);
  ctx.fillStyle = "#F4EDDE";
  ctx.fillRect(0, 0, UNIT_WIDTH, UNIT_HEIGHT);
  return [canvas, ctx];
}

function finish(canvas: HTMLCanvasElement): CanvasTexture {
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

/** Faint horizontal filler bars standing in for unreadable body text. */
function rules(ctx: CanvasRenderingContext2D, x0: number, y0: number, n: number, w: number, gap: number, alpha: number) {
  ctx.fillStyle = `rgba(34,27,18,${alpha})`;
  for (let i = 0; i < n; i++) {
    ctx.fillRect(x0, y0 + i * gap, w * (0.45 + Math.random() * 0.55), 4.5);
  }
}

function head(ctx: CanvasRenderingContext2D, kicker: string, title: string, size?: number) {
  ctx.fillStyle = "#8A5A2B";
  ctx.font = "700 13px Archivo, sans-serif";
  ctx.fillText(kicker, 34, 54);
  ctx.fillStyle = "#221B12";
  ctx.font = `300 ${size ?? 28}px Newsreader, Georgia, serif`;
  ctx.fillText(title, 34, 92);
  ctx.strokeStyle = "rgba(34,27,18,.28)";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(34, 112);
  ctx.lineTo(406, 112);
  ctx.stroke();
}

function drawCv(spec: Extract<TextureSpec, { kind: "cv" }>): CanvasTexture {
  const [canvas, x] = paper();
  x.fillStyle = "rgba(34,27,18,.10)";
  x.fillRect(318, 30, 88, 108);
  x.fillStyle = "#221B12";
  x.font = "300 34px Newsreader, Georgia, serif";
  x.fillText(spec.name, 34, 70);
  x.fillStyle = "#8A5A2B";
  x.font = "700 14px Archivo, sans-serif";
  x.fillText("CURRICULUM VITAE", 34, 94);
  x.strokeStyle = "rgba(34,27,18,.3)";
  x.lineWidth = 1.4;
  x.beginPath();
  x.moveTo(34, 150);
  x.lineTo(406, 150);
  x.stroke();
  (
    [
      ["EXPERIENCE", 178, 7],
      ["CERTIFICATIONS", 384, 3],
      ["EDUCATION", 492, 3],
    ] as const
  ).forEach(([h, y, n]) => {
    x.fillStyle = "#8A5A2B";
    x.font = "700 12px Archivo, sans-serif";
    x.fillText(h, 34, y);
    rules(x, 34, y + 16, n, 290, 26, 0.32);
    x.fillStyle = "rgba(34,27,18,.2)";
    for (let i = 0; i < n; i++) x.fillRect(352, y + 16 + i * 26, 54, 4.5);
  });
  x.fillStyle = "rgba(138,90,43,.5)";
  x.fillRect(34, 220, 290, 4.5);
  return finish(canvas);
}

function drawCert(spec: Extract<TextureSpec, { kind: "cert" }>): CanvasTexture {
  const [canvas, x] = paper();
  x.strokeStyle = "rgba(138,90,43,.55)";
  x.lineWidth = 4;
  x.strokeRect(22, 22, 396, 556);
  x.lineWidth = 1.2;
  x.strokeRect(32, 32, 376, 536);
  x.textAlign = "center";
  x.fillStyle = "#8A5A2B";
  x.font = "700 13px Archivo, sans-serif";
  x.fillText(spec.kicker, 220, 92);
  x.fillStyle = "#221B12";
  x.font = "300 38px Newsreader, Georgia, serif";
  x.fillText(spec.title, 220, 164);
  x.font = "300 19px Newsreader, Georgia, serif";
  x.fillText(spec.sub, 220, 196);
  x.strokeStyle = "rgba(34,27,18,.3)";
  x.lineWidth = 1.2;
  x.beginPath();
  x.arc(220, 300, 58, 0, 7);
  x.stroke();
  x.beginPath();
  x.arc(220, 300, 48, 0, 7);
  x.stroke();
  for (let i = 0; i < 36; i++) {
    const t = (i / 36) * Math.PI * 2;
    x.beginPath();
    x.moveTo(220 + Math.cos(t) * 48, 300 + Math.sin(t) * 48);
    x.lineTo(220 + Math.cos(t) * 58, 300 + Math.sin(t) * 58);
    x.stroke();
  }
  x.fillStyle = "rgba(138,90,43,.75)";
  x.font = "700 15px Archivo, sans-serif";
  x.fillText(spec.badge ?? "VALID", 220, 306);
  x.textAlign = "left";
  rules(x, 74, 392, 3, 290, 26, 0.3);
  x.strokeStyle = "rgba(34,27,18,.4)";
  x.beginPath();
  x.moveTo(74, 506);
  x.lineTo(226, 506);
  x.stroke();
  x.beginPath();
  x.moveTo(254, 506);
  x.lineTo(366, 506);
  x.stroke();
  return finish(canvas);
}

function drawReport(spec: Extract<TextureSpec, { kind: "report" }>): CanvasTexture {
  const [canvas, x] = paper();
  x.fillStyle = "#8A5A2B";
  x.font = "700 13px Archivo, sans-serif";
  x.fillText("PAST PERFORMANCE", 34, 54);
  x.fillStyle = "#221B12";
  x.font = "300 30px Newsreader, Georgia, serif";
  x.fillText(spec.title1, 34, 96);
  x.fillText(spec.title2, 34, 128);
  x.strokeStyle = "rgba(34,27,18,.28)";
  x.lineWidth = 1.4;
  x.beginPath();
  x.moveTo(34, 148);
  x.lineTo(406, 148);
  x.stroke();
  spec.rows.forEach(([k, v], i) => {
    x.fillStyle = "rgba(34,27,18,.4)";
    x.font = "700 10px Archivo, sans-serif";
    x.fillText(k, 34, 178 + i * 38);
    x.fillStyle = "#221B12";
    x.font = "400 16px Archivo, sans-serif";
    x.fillText(v, 140, 178 + i * 38);
  });
  rules(x, 34, 352, 8, 340, 26, 0.3);
  x.fillStyle = "rgba(138,90,43,.5)";
  x.fillRect(34, 378, 340, 4.5);
  return finish(canvas);
}

function drawTable(spec: Extract<TextureSpec, { kind: "table" }>): CanvasTexture {
  const [canvas, x] = paper();
  head(x, spec.kicker, spec.title);
  const cols = spec.cols ?? 4;
  const y0 = 142,
    rowHeight = 30,
    rows = 13,
    colWidth = 372 / cols;
  x.fillStyle = "rgba(138,90,43,.16)";
  x.fillRect(34, y0, 372, rowHeight);
  x.fillStyle = "rgba(138,90,43,.14)";
  x.fillRect(34, y0 + rowHeight * (4 + (spec.title.length % 6)), 372, rowHeight);
  x.strokeStyle = "rgba(34,27,18,.16)";
  x.lineWidth = 1;
  for (let r = 0; r <= rows; r++) {
    x.beginPath();
    x.moveTo(34, y0 + r * rowHeight);
    x.lineTo(406, y0 + r * rowHeight);
    x.stroke();
  }
  for (let k = 1; k < cols; k++) {
    x.beginPath();
    x.moveTo(34 + k * colWidth, y0);
    x.lineTo(34 + k * colWidth, y0 + rows * rowHeight);
    x.stroke();
  }
  for (let r = 1; r < rows; r++) {
    for (let k = 0; k < cols; k++) {
      x.fillStyle = `rgba(34,27,18,${k ? 0.24 : 0.34})`;
      x.fillRect(42 + k * colWidth, y0 + r * rowHeight + 13, (colWidth - 18) * (0.35 + Math.random() * 0.6), 4.5);
    }
  }
  return finish(canvas);
}

function drawLetter(spec: Extract<TextureSpec, { kind: "letter" }>): CanvasTexture {
  const [canvas, x] = paper();
  x.fillStyle = "rgba(138,90,43,.8)";
  x.fillRect(34, 40, 372, 6);
  x.fillStyle = "#8A5A2B";
  x.font = "700 13px Archivo, sans-serif";
  x.fillText(spec.kicker, 34, 78);
  x.fillStyle = "#221B12";
  x.font = "300 26px Newsreader, Georgia, serif";
  x.fillText(spec.title, 34, 114);
  rules(x, 254, 146, 4, 150, 20, 0.26);
  rules(x, 34, 236, 6, 372, 24, 0.28);
  rules(x, 34, 396, 5, 372, 24, 0.28);
  x.strokeStyle = "rgba(34,27,18,.55)";
  x.lineWidth = 2;
  x.beginPath();
  x.moveTo(40, 540);
  for (let i = 0; i < 9; i++) x.quadraticCurveTo(52 + i * 14, 510 + (i % 2) * 40, 62 + i * 14, 532);
  x.stroke();
  if (spec.stamp) {
    x.strokeStyle = "rgba(158,59,43,.55)";
    x.lineWidth = 3;
    x.save();
    x.translate(330, 510);
    x.rotate(-0.22);
    x.strokeRect(-58, -24, 116, 48);
    x.fillStyle = "rgba(158,59,43,.65)";
    x.font = "700 15px Archivo, sans-serif";
    x.textAlign = "center";
    x.fillText(spec.stamp, 0, 6);
    x.restore();
  }
  return finish(canvas);
}

function drawPolicy(spec: Extract<TextureSpec, { kind: "policy" }>): CanvasTexture {
  const [canvas, x] = paper();
  head(x, spec.kicker, spec.title);
  [140, 262, 384, 488].forEach((y, i) => {
    x.fillStyle = "rgba(138,90,43,.8)";
    x.font = "700 14px Archivo, sans-serif";
    x.fillText(`${i + 1}.`, 34, y + 10);
    x.fillStyle = "rgba(34,27,18,.42)";
    x.fillRect(60, y, 160, 6);
    rules(x, 60, y + 26, i === 3 ? 2 : 3, 346, 24, 0.26);
  });
  return finish(canvas);
}

function drawOrg(spec: Extract<TextureSpec, { kind: "org" }>): CanvasTexture {
  const [canvas, x] = paper();
  head(x, spec.kicker, spec.title);
  x.strokeStyle = "rgba(34,27,18,.35)";
  x.lineWidth = 1.4;
  const box = (cx: number, cy: number, hi: boolean) => {
    x.fillStyle = hi ? "rgba(138,90,43,.2)" : "rgba(34,27,18,.06)";
    x.fillRect(cx - 44, cy - 20, 88, 40);
    x.strokeRect(cx - 44, cy - 20, 88, 40);
    x.fillStyle = "rgba(34,27,18,.38)";
    x.fillRect(cx - 28, cy - 3, 56, 4.5);
  };
  const levels = [[220], [110, 330], [70, 170, 270, 370]];
  const rowY = [180, 300, 420];
  levels.forEach((row, r) =>
    row.forEach((cx) => {
      if (r) {
        const parentRow = levels[r - 1];
        const px = parentRow.reduce((best, p) => (Math.abs(p - cx) < Math.abs(best - cx) ? p : best), parentRow[0]);
        x.beginPath();
        x.moveTo(px, rowY[r - 1] + 20);
        x.lineTo(px, rowY[r] - 40);
        x.lineTo(cx, rowY[r] - 40);
        x.lineTo(cx, rowY[r] - 20);
        x.stroke();
      }
      box(cx, rowY[r], r === 0);
    }),
  );
  rules(x, 34, 500, 3, 372, 24, 0.24);
  return finish(canvas);
}

function drawDrawing(spec: Extract<TextureSpec, { kind: "drawing" }>): CanvasTexture {
  const [canvas, x] = paper();
  x.fillStyle = "#8A5A2B";
  x.font = "700 13px Archivo, sans-serif";
  x.fillText("DRAWING", 34, 54);
  x.fillStyle = "#221B12";
  x.font = "300 28px Newsreader, Georgia, serif";
  x.fillText(spec.title, 34, 92);
  x.strokeStyle = "rgba(34,27,18,.28)";
  x.lineWidth = 1.4;
  x.beginPath();
  x.moveTo(34, 112);
  x.lineTo(406, 112);
  x.stroke();
  x.strokeStyle = "rgba(60,88,94,.55)";
  x.lineWidth = 1.2;
  x.strokeRect(24, 24, 392, 552);
  x.strokeStyle = "rgba(60,88,94,.18)";
  for (let i = 1; i < 14; i++) {
    x.beginPath();
    x.moveTo(24, 24 + i * 40);
    x.lineTo(416, 24 + i * 40);
    x.stroke();
  }
  for (let i = 1; i < 10; i++) {
    x.beginPath();
    x.moveTo(24 + i * 40, 24);
    x.lineTo(24 + i * 40, 576);
    x.stroke();
  }
  x.strokeStyle = "rgba(40,62,68,.8)";
  x.lineWidth = 2.2;
  x.strokeRect(70, 90, 170, 120);
  x.strokeRect(260, 150, 110, 190);
  x.strokeRect(90, 270, 120, 90);
  x.setLineDash([10, 7]);
  x.beginPath();
  x.moveTo(30, 420);
  x.bezierCurveTo(160, 380, 300, 470, 410, 410);
  x.stroke();
  x.beginPath();
  x.moveTo(30, 440);
  x.bezierCurveTo(160, 400, 300, 490, 410, 430);
  x.stroke();
  x.setLineDash([]);
  x.beginPath();
  x.moveTo(372, 70);
  x.lineTo(384, 40);
  x.lineTo(396, 70);
  x.closePath();
  x.stroke();
  x.fillStyle = "rgba(244,237,222,1)";
  x.fillRect(256, 486, 160, 90);
  x.strokeRect(256, 486, 160, 90);
  x.fillStyle = "rgba(40,62,68,.8)";
  x.font = "700 12px Archivo, sans-serif";
  x.fillText(spec.title, 268, 510);
  x.fillStyle = "rgba(40,62,68,.4)";
  x.fillRect(268, 526, 120, 4);
  x.fillRect(268, 542, 90, 4);
  x.fillRect(268, 558, 104, 4);
  return finish(canvas);
}

function drawGantt(spec: Extract<TextureSpec, { kind: "gantt" }>): CanvasTexture {
  const [canvas, x] = paper();
  head(x, spec.kicker, spec.title);
  x.strokeStyle = "rgba(34,27,18,.12)";
  x.lineWidth = 1;
  for (let k = 0; k < 9; k++) {
    x.beginPath();
    x.moveTo(150 + k * 32, 136);
    x.lineTo(150 + k * 32, 560);
    x.stroke();
  }
  for (let r = 0; r < 14; r++) {
    const y = 150 + r * 29,
      s = 150 + ((r * 37) % 180),
      w = 40 + ((r * 53) % 120);
    x.fillStyle = "rgba(34,27,18,.3)";
    x.fillRect(34, y + 8, 88 * (0.5 + ((r * 29) % 10) / 20), 4.5);
    x.fillStyle = r % 5 === 2 ? "rgba(138,90,43,.7)" : "rgba(34,27,18,.34)";
    x.fillRect(s, y, w, 14);
  }
  x.fillStyle = "rgba(158,59,43,.7)";
  x.save();
  x.translate(360, 556);
  x.rotate(Math.PI / 4);
  x.fillRect(-7, -7, 14, 14);
  x.restore();
  return finish(canvas);
}

export function generateDocumentTexture(spec: TextureSpec): CanvasTexture {
  switch (spec.kind) {
    case "cv":
      return drawCv(spec);
    case "cert":
      return drawCert(spec);
    case "report":
      return drawReport(spec);
    case "table":
      return drawTable(spec);
    case "letter":
      return drawLetter(spec);
    case "policy":
      return drawPolicy(spec);
    case "org":
      return drawOrg(spec);
    case "drawing":
      return drawDrawing(spec);
    case "gantt":
      return drawGantt(spec);
  }
}
