(function installVditorRuntime(root) {
  'use strict';

  const LOCAL_CDN = './vendor/vditor';
  const PREVIEW_CLASS = 'wandao-vditor-preview';
  const pendingPreviews = new Map();
  const editors = new Map();
  let previewSequence = 0;

  // Keep the writing, formatting and mode-switching tools that belong inside
  // Wandao. Low-frequency presentation tools and controls without a Wandao
  // persistence contract stay out of this compact toolbar.
  const FULL_TOOLBAR = [
    'headings', 'bold', 'italic', 'strike', 'link', '|',
    'list', 'ordered-list', 'check', 'quote', 'line',
    'code', 'inline-code', 'table', '|',
    'undo', 'redo', 'edit-mode', 'both', 'preview', 'fullscreen', 'outline'
  ];

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function currentTheme() {
    return root.document?.body?.dataset?.theme === 'dark' ? 'dark' : 'light';
  }

  function allowedAnchor(value) {
    return /^#[A-Za-z0-9_\u4e00-\u9fff_.:-]+$/.test(String(value || ''));
  }

  function unwrap(element) {
    const parent = element?.parentNode;
    if (!parent) return;
    while (element.firstChild) parent.insertBefore(element.firstChild, element);
    parent.removeChild(element);
  }

  function sanitizeRenderedHtml(html, options = {}) {
    const document = root.document;
    if (!document?.createElement) return String(html || '');
    const container = document.createElement('div');
    container.innerHTML = String(html || '');
    const externalLinkAttribute = options.externalLinkAttribute || 'data-external-link';
    const imageAttribute = Object.prototype.hasOwnProperty.call(options, 'imageAttribute')
      ? options.imageAttribute
      : '';
    const resolveImageSource = typeof options.resolveImageSource === 'function'
      ? options.resolveImageSource
      : (source) => source;
    const allowImageSource = typeof options.allowImageSource === 'function'
      ? options.allowImageSource
      : () => true;

    container.querySelectorAll('a').forEach((anchor) => {
      const href = String(anchor.getAttribute('href') || '').trim();
      if (/^https:\/\//i.test(href)) {
        anchor.setAttribute(externalLinkAttribute, 'true');
      } else if (!allowedAnchor(href)) {
        unwrap(anchor);
      }
    });

    container.querySelectorAll('img').forEach((image) => {
      const source = String(
        image.getAttribute('data-src')
        || image.getAttribute('src')
        || image.getAttribute('data-original')
        || ''
      ).trim();
      const resolved = String(resolveImageSource(source) || '').trim();
      if (!resolved || !allowImageSource(resolved)) {
        image.remove();
        return;
      }
      if (options.imageClass) image.className = options.imageClass;
      if (imageAttribute) {
        image.setAttribute(imageAttribute, resolved);
        image.removeAttribute('src');
        image.removeAttribute('data-src');
      } else {
        image.setAttribute('src', resolved);
        image.removeAttribute('data-src');
      }
      image.setAttribute('loading', 'lazy');
    });
    return container.innerHTML;
  }

  function previewOptions(options = {}) {
    const theme = options.theme || currentTheme();
    const dark = theme === 'dark';
    const cdn = options.cdn || LOCAL_CDN;
    const userAfter = options.after;
    return {
      cdn,
      mode: dark ? 'dark' : 'light',
      lang: 'zh_CN',
      anchor: 0,
      emojiPath: `${cdn}/dist/images/emoji`,
      lazyLoadImage: `${cdn}/dist/images/img-loading.svg`,
      hljs: {
        enable: true,
        style: dark ? 'github-dark' : 'github',
        lineNumber: true
      },
      math: { engine: 'KaTeX' },
      render: {
        media: { enable: true }
      },
      markdown: {
        sanitize: true,
        footnotes: true,
        gfmAutoLink: true,
        callout: true,
        codeBlockPreview: true,
        mathBlockPreview: true,
        toc: false
      },
      theme: {
        current: dark ? 'dark' : 'light',
        path: `${cdn}/dist/css/content-theme`
      },
      transform: (html) => sanitizeRenderedHtml(html, options),
      after: () => {
        if (typeof userAfter === 'function') userAfter();
      }
    };
  }

  function placeholder(source, options = {}) {
    const id = `vditor-${Date.now()}-${previewSequence += 1}`;
    pendingPreviews.set(id, { source: String(source || ''), options: { ...options } });
    return `<div class="${PREVIEW_CLASS}" data-vditor-preview-id="${escapeHtml(id)}" role="document"></div>`;
  }

  async function renderPreview(element, source, options = {}) {
    if (!element) return false;
    if (!root.Vditor || typeof root.Vditor.preview !== 'function') {
      throw new Error('Vditor 渲染器尚未加载。');
    }
    element.classList.add('vditor-reset', PREVIEW_CLASS);
    element.setAttribute('aria-busy', 'true');
    await root.Vditor.preview(element, String(source || ''), previewOptions(options));
    element.setAttribute('aria-busy', 'false');
    return true;
  }

  async function mountQueued(container) {
    const elements = Array.from(container?.querySelectorAll?.(`[data-vditor-preview-id]`) || []);
    await Promise.all(elements.map(async (element) => {
      const id = element.dataset.vditorPreviewId || '';
      const pending = pendingPreviews.get(id);
      pendingPreviews.delete(id);
      if (!pending) return;
      try {
        await renderPreview(element, pending.source, pending.options);
        element.dispatchEvent(new root.CustomEvent('wandao-vditor-ready'));
      } catch (error) {
        element.setAttribute('aria-busy', 'false');
        element.innerHTML = `<div class="wandao-vditor-error"><strong>Markdown 渲染失败</strong><p>${escapeHtml(error?.message || String(error))}</p></div>`;
      }
    }));
  }

  function editorOptions(value, options = {}) {
    const dark = currentTheme() === 'dark';
    const userInput = options.input;
    const userAfter = options.after;
    const cdn = options.cdn || LOCAL_CDN;
    const sharedPreview = previewOptions(options);
    return {
      cdn,
      lang: 'zh_CN',
      mode: options.mode || 'ir',
      theme: dark ? 'dark' : 'classic',
      icon: 'ant',
      value: String(value || ''),
      cache: { enable: false },
      toolbar: FULL_TOOLBAR,
      toolbarConfig: { hide: false, pin: false },
      counter: { enable: true, type: 'markdown' },
      outline: { enable: false, position: 'right' },
      resize: { enable: false },
      image: { isPreview: true },
      preview: {
        delay: 180,
        mode: 'both',
        markdown: sharedPreview.markdown,
        hljs: sharedPreview.hljs,
        math: sharedPreview.math,
        theme: sharedPreview.theme,
        transform: sharedPreview.transform,
        render: sharedPreview.render
      },
      input: (markdown) => {
        if (typeof userInput === 'function') userInput(String(markdown || ''));
      },
      after: () => {
        if (typeof userAfter === 'function') userAfter();
      }
    };
  }

  function mountEditor(element, value, options = {}) {
    if (!element) return null;
    if (!root.Vditor) throw new Error('Vditor 编辑器尚未加载。');
    destroyEditor(element);
    const editor = new root.Vditor(element, editorOptions(value, options));
    editors.set(element, editor);
    return editor;
  }

  function editorFor(element) {
    return editors.get(element) || null;
  }

  function destroyEditor(element) {
    const editor = editors.get(element);
    if (!editor) return;
    try { editor.destroy(); } catch (_) {}
    editors.delete(element);
  }

  function destroyAllEditors() {
    Array.from(editors.keys()).forEach(destroyEditor);
  }

  root.WandaoVditor = Object.freeze({
    FULL_TOOLBAR,
    destroyAllEditors,
    destroyEditor,
    editorFor,
    mountEditor,
    mountQueued,
    placeholder,
    renderPreview
  });
})(typeof window !== 'undefined' ? window : globalThis);
