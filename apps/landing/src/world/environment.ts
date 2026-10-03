import { CanvasTexture, EquirectangularReflectionMapping, PMREMGenerator, SRGBColorSpace, type Texture, type WebGLRenderer } from "three";

/**
 * A small procedural warm-gradient equirectangular map, used only as an
 * environment/reflection source (never rendered directly) so the document
 * sheets' `MeshStandardMaterial` picks up soft ambient reflections instead
 * of looking flat. Ported from reference-landing.html:826-838.
 */
export function createEnvironmentTexture(renderer: WebGLRenderer): Texture {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 256;
  const ctx = canvas.getContext("2d")!;

  const vertical = ctx.createLinearGradient(0, 0, 0, 256);
  vertical.addColorStop(0, "#5D462E");
  vertical.addColorStop(0.44, "#241B13");
  vertical.addColorStop(1, "#090706");
  ctx.fillStyle = vertical;
  ctx.fillRect(0, 0, 512, 256);

  const glow = ctx.createRadialGradient(140, 44, 0, 140, 44, 180);
  glow.addColorStop(0, "rgba(255,228,186,1)");
  glow.addColorStop(1, "rgba(255,228,186,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, 512, 256);

  const equirect = new CanvasTexture(canvas);
  equirect.mapping = EquirectangularReflectionMapping;
  equirect.colorSpace = SRGBColorSpace;

  const pmrem = new PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const envMap = pmrem.fromEquirectangular(equirect).texture;
  equirect.dispose();
  pmrem.dispose();
  return envMap;
}
