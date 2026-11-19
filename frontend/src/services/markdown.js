// Minimal Markdown for assistant answers: headings, bold, inline code, lists, quotes, tables, paragraphs.
// Everything is HTML-escaped first, so model output can never inject markup or scripts.

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const escape = (text) => text.replace(/[&<>"']/g, (c) => ESCAPES[c]);

function inline(text) {
  return escape(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => inline(c.trim()));
const isDivider = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

export function renderMarkdown(source) {
  const html = [];
  let list = null, paragraph = [], table = null;
  const flushTable = () => {
    if (table) {
      const [head, ...body] = table;
      html.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>${
        body.map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
    }
    table = null;
  };
  const flushParagraph = () => { if (paragraph.length) html.push(`<p>${paragraph.join('<br>')}</p>`); paragraph = []; };
  const flushList = () => { if (list) html.push(`<${list.tag}>${list.items.map((i) => `<li>${i}</li>`).join('')}</${list.tag}>`); list = null; };
  for (const raw of String(source || '').split(/\r?\n/)) {
    const line = raw.trimEnd();
    let match;
    if (line.trim().startsWith('|')) {
      flushParagraph(); flushList();
      if (table && isDivider(line)) continue;
      (table ||= []).push(cells(line));
      continue;
    }
    flushTable();
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    if ((match = /^(#{1,4})\s+(.*)$/.exec(line))) {
      flushParagraph(); flushList();
      const level = Math.min(match[1].length + 2, 6); // keep answer headings below the page headings
      html.push(`<h${level}>${inline(match[2])}</h${level}>`);
    } else if ((match = /^\s*(?:[-*]|(\d+)[.、])\s+(.*)$/.exec(line))) {
      flushParagraph();
      const tag = match[1] ? 'ol' : 'ul';
      if (list && list.tag !== tag) flushList();
      list ||= { tag, items: [] };
      list.items.push(inline(match[2]));
    } else if ((match = /^>\s?(.*)$/.exec(line))) {
      flushParagraph(); flushList();
      html.push(`<blockquote>${inline(match[1])}</blockquote>`);
    } else if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      flushParagraph(); flushList();
      html.push('<hr>');
    } else {
      flushList();
      paragraph.push(inline(line));
    }
  }
  flushParagraph(); flushList(); flushTable();
  return html.join('');
}
