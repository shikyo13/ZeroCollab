import { memo, useMemo } from 'react';

declare global {
  interface Window {
    /** Safe chat formatter, loaded by index.html before the workspace bundle. */
    renderMarkdown?: (text: string) => string;
  }
}

const PREVIEW_LENGTH = 140;

/** Extract readable text from the same rendering used by full decision text. */
function plainPreview(html: string): string {
  const template = document.createElement('template');
  template.innerHTML = html;
  for (const image of template.content.querySelectorAll('img')) image.replaceWith(image.alt);
  for (const block of template.content.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, pre, blockquote, br, tr, td, th')) {
    block.after(document.createTextNode(' '));
  }
  return (template.content.textContent || '').replace(/\s+/g, ' ').trim();
}

/** Reuse chat's HTML escaping and URL filtering; never render agent HTML directly. */
export const DecisionText = memo(function DecisionText({ text = '', compact = false }: { text?: string; compact?: boolean }) {
  const html = useMemo(() => window.renderMarkdown?.(text), [text]);
  const preview = useMemo(() => {
    if (!compact) return '';
    const plain = html === undefined ? text : plainPreview(html);
    const chars = [...plain];
    return chars.length > PREVIEW_LENGTH ? chars.slice(0, PREVIEW_LENGTH).join('') + '…' : plain;
  }, [compact, html, text]);
  if (compact) return <p className="decision-text">{preview}</p>;
  if (html === undefined) return <div className="decision-text">{text}</div>;
  return <div className="decision-text decision-markdown msg-text" dangerouslySetInnerHTML={{ __html: html }} />;
});
