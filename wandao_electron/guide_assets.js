const fs = require('fs');
const path = require('path');

const GUIDE_IMAGE_MIME_TYPES = Object.freeze({
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp'
});
const DEFAULT_MAX_GUIDE_IMAGE_BYTES = 5 * 1024 * 1024;

function isInsidePath(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

function readGuideImageDataUrl(providerRoot, relativePath, options = {}) {
  const root = path.resolve(String(providerRoot || ''));
  const rawPath = String(relativePath || '').trim();
  if (!rawPath || path.isAbsolute(rawPath) || /^[a-z][a-z0-9+.-]*:/i.test(rawPath)) {
    throw new Error('教程图片路径必须是 Provider 内的相对路径');
  }

  const resolved = path.resolve(root, rawPath);
  if (!isInsidePath(root, resolved)) {
    throw new Error('教程图片路径不能超出 Provider 目录');
  }

  const extension = path.extname(resolved).toLowerCase();
  const mimeType = GUIDE_IMAGE_MIME_TYPES[extension];
  if (!mimeType) {
    throw new Error('不支持的教程图片格式');
  }
  if (!fs.existsSync(resolved)) {
    throw new Error('教程图片不存在');
  }

  const stat = fs.statSync(resolved);
  const maxBytes = Number.isFinite(options.maxBytes) ? options.maxBytes : DEFAULT_MAX_GUIDE_IMAGE_BYTES;
  if (!stat.isFile()) {
    throw new Error('教程图片必须是文件');
  }
  if (stat.size > maxBytes) {
    throw new Error('教程图片大小超过限制');
  }

  return `data:${mimeType};base64,${fs.readFileSync(resolved).toString('base64')}`;
}

module.exports = {
  DEFAULT_MAX_GUIDE_IMAGE_BYTES,
  GUIDE_IMAGE_MIME_TYPES,
  readGuideImageDataUrl
};
