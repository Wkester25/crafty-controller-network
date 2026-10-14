// Links the renderer found in server output (app/classes/helpers/links.py).
// The server operator chose where they point, so confirm before following
// one. The real href keeps copy-link and middle-click working without this.
(function () {
  const strings = {
    title: 'Leaving Crafty',
    message: 'This link opens a site outside of Crafty.',
    open: 'Open link',
    cancel: 'Cancel',
    ...window.termLinkStrings,
  };

  function escapeHtml(text) {
    const node = document.createElement('span');
    node.textContent = text;
    return node.innerHTML;
  }

  // Emphasise the host and dim the rest, the way a URL bar does, so the
  // part that decides where you land is the part that stands out.
  function describe(url) {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return escapeHtml(url);
    }
    const credentials = parsed.username ? `${parsed.username}@` : '';
    const scheme = `${parsed.protocol}//${credentials}`;
    const rest = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    return `<span class="text-muted">${escapeHtml(scheme)}</span>` +
      `<strong>${escapeHtml(parsed.host)}</strong>` +
      `<span class="text-muted">${escapeHtml(rest)}</span>`;
  }

  function confirmExternalLink(event) {
    const link = event.target.closest('a.term-link');
    if (!link) return;

    // A modified click already asked for a new tab or a saved copy.
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey ||
      event.altKey) {
      return;
    }

    event.preventDefault();
    const url = link.href;

    bootbox.confirm({
      title: strings.title,
      message: `<p>${escapeHtml(strings.message)}</p>` +
        `<p class="mb-0 text-break">${describe(url)}</p>`,
      buttons: {
        confirm: { label: strings.open, className: 'btn-info' },
        cancel: { label: strings.cancel, className: 'btn-secondary' },
      },
      callback: function (result) {
        if (result) window.open(url, '_blank', 'noopener,noreferrer');
      },
    });
  }

  document.addEventListener('click', confirmExternalLink);
})();
