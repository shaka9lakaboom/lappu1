/**
 * Visible text of a rendered message subtree, computed from the DOM tree
 * rather than `innerText` so results do not depend on layout (and match in
 * tests). Controls, icons and hidden nodes are skipped; block elements become
 * line breaks; <pre> keeps its whitespace.
 *
 * Only text nodes are read. Nothing is evaluated or re-inserted into any page.
 */

const NL = String.fromCharCode(10);
const TAB = String.fromCharCode(9);
const WHITESPACE_RUN = /\s+/g;
const TRAILING_BLANKS = new RegExp(`[ ${TAB}]+$`);
const EXCESS_BLANK_LINES = new RegExp(`${NL}{3,}`, 'g');

const SKIPPED_TAGS = new Set([
  'BUTTON', 'SVG', 'STYLE', 'SCRIPT', 'NOSCRIPT', 'TEMPLATE', 'IFRAME', 'CANVAS', 'VIDEO', 'AUDIO',
  'INPUT', 'TEXTAREA', 'SELECT', 'OBJECT', 'EMBED',
]);
const BLOCK_TAGS = new Set([
  'P', 'DIV', 'PRE', 'LI', 'UL', 'OL', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE', 'TABLE',
  'THEAD', 'TBODY', 'TR', 'SECTION', 'ARTICLE', 'HEADER', 'FOOTER', 'FIGURE', 'FIGCAPTION', 'HR', 'DL', 'DT', 'DD',
]);
/** Blocks followed by a blank line, as a browser renders paragraph spacing. */
const SPACED_BLOCKS = new Set(['P', 'PRE', 'BLOCKQUOTE', 'TABLE', 'UL', 'OL']);

function isHidden(element: Element): boolean {
  return (
    element.hasAttribute('hidden') ||
    element.getAttribute('aria-hidden') === 'true' ||
    element.classList.contains('sr-only') ||
    element.hasAttribute('popover')
  );
}

function preservesWhitespace(element: Element): boolean {
  return element.tagName.toUpperCase() === 'PRE' || /(^|\s)whitespace-pre(-wrap|-line)?(\s|$)/.test(element.getAttribute('class') ?? '');
}

/** `preformatted`: the root renders with white-space: pre-wrap (e.g. user-typed text). */
export function renderedText(root: Element, { preformatted = false }: { preformatted?: boolean } = {}): string {
  let out = '';
  const atLineStart = () => out.length === 0 || out.endsWith(NL);
  const endLine = () => {
    out = out.replace(/ +$/, '');
    if (!atLineStart()) out += NL;
  };

  const walk = (node: Node, inPre: boolean): void => {
    if (node.nodeType === 3 /* TEXT_NODE */) {
      const text = node.nodeValue ?? '';
      if (inPre) {
        out += text;
      } else {
        const collapsed = text.replace(WHITESPACE_RUN, ' ');
        out += atLineStart() || out.endsWith(' ') ? collapsed.replace(/^ /, '') : collapsed;
      }
      return;
    }
    if (node.nodeType !== 1 /* ELEMENT_NODE */) return; // comments, processing instructions
    const element = node as Element;
    const tag = element.tagName.toUpperCase();
    if (element !== root && (SKIPPED_TAGS.has(tag) || isHidden(element))) return;
    if (tag === 'BR') {
      out += NL;
      return;
    }
    const block = BLOCK_TAGS.has(tag);
    if (block) endLine();
    const pre = inPre || preservesWhitespace(element);
    for (const child of Array.from(element.childNodes)) walk(child, pre);
    if (block) endLine();
    if (tag === 'TD' || tag === 'TH') out += TAB;
    if (SPACED_BLOCKS.has(tag)) out += NL;
  };

  walk(root, preformatted);
  return out
    .split(NL)
    .map((line) => line.replace(TRAILING_BLANKS, ''))
    .join(NL)
    .replace(EXCESS_BLANK_LINES, NL + NL)
    .trim();
}
