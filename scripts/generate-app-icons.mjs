import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import sharp from "sharp";

const root = path.resolve(import.meta.dirname, "..");
const source = path.join(root, "web", "app", "icon.svg");
const output = path.join(
  root,
  "macos",
  "ElectronicMail",
  "ElectronicMail",
  "Mac",
  "Assets.xcassets",
  "AppIcon.appiconset",
);
const icons = [
  ["icon_16x16.png", 16],
  ["icon_16x16@2x.png", 32],
  ["icon_32x32.png", 32],
  ["icon_32x32@2x.png", 64],
  ["icon_128x128.png", 128],
  ["icon_128x128@2x.png", 256],
  ["icon_256x256.png", 256],
  ["icon_256x256@2x.png", 512],
  ["icon_512x512.png", 512],
  ["icon_512x512@2x.png", 1024],
];

await mkdir(output, { recursive: true });
const svg = await readFile(source);
for (const [filename, size] of icons) {
  await sharp(svg).resize(size, size).png().toFile(path.join(output, filename));
}

console.log(`Generated ${icons.length} macOS app icons from ${path.relative(root, source)}`);
