import { memo, useState, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import { Copy, Check, Paperclip } from 'lucide-react';
import { imagesFor, documentsFor } from '../../lib/store';
import { AudioPlayer } from './AudioPlayer';
import { ResearchTimeline } from './ResearchTimeline';
import { rehypeCitations } from '../../lib/rehype-citations';
import { XRayFooter } from './XRayFooter';
import { LinkPreviewCard } from './LinkPreviewCard';
import {
  externalLinkAttributes,
  selectLinkPreview,
  selectSearchImages,
} from '../../lib/link-preview';
import { protectCurrencyFromMath } from '../../lib/currency-math';
import type { ChatMessage } from '../../types';

function stripThinkTags(text: string): string {
  let cleaned = text.replace(/<think>[\s\S]*?<\/think>\s*/gi, '');
  cleaned = cleaned.replace(/^[\s\S]*?<\/think>\s*/i, '');
  return cleaned.trim();
}

interface Props {
  message: ChatMessage;
  isLive?: boolean;
}

function getTextContent(node: any): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(getTextContent).join('');
  }
  if (node?.props?.children) {
    return getTextContent(node.props.children);
  }
  return '';
}

function CodeBlockPre({ children, ...props }: any) {
  const [copied, setCopied] = useState(false);
  const codeElement = Array.isArray(children) ? children[0] : children;
  const className = codeElement?.props?.className || '';
  const match = /language-([\w-]+)/.exec(className);
  const lang = match ? match[1] : '';
  const code = getTextContent(codeElement?.props?.children).replace(/\n$/, '');

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className="code-block-wrapper relative my-3"
      style={{ borderRadius: 'var(--radius-md)', overflow: 'hidden' }}
    >
      <div
        className="flex items-center justify-between px-4 py-1.5 text-xs"
        style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text-tertiary)' }}
      >
        <span className="font-mono">{lang || 'code'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-0.5 rounded transition-colors cursor-pointer"
          style={{ color: 'var(--color-text-tertiary)' }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-text-secondary)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-tertiary)')}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre {...props} style={{ margin: 0, borderRadius: 0 }}>
        {children}
      </pre>
    </div>
  );
}

function CopyMessageButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
      style={{ color: 'var(--color-text-tertiary)' }}
      title="Copy message"
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}

function MessageBubbleComponent({ message, isLive = false }: Props) {
  const isUser = message.role === 'user';
  // Session-only, keyed by message id — see the registry in store.ts.
  const sessionImages = imagesFor(message.id);
  // Shown on the bubble so an attachment is visible where it is actually in
  // effect. Without it there was no way to tell a turn that carried the paper
  // from one that had quietly lost it -- which is exactly the confusion the
  // user hit, twice, with only the token count as evidence.
  const attachedDocuments = message.documents ?? documentsFor(message.id) ?? [];

  const cleanContent = useMemo(() => stripThinkTags(message.content), [message.content]);
  // Escaped only for rendering. Copy must still yield "$200", not "\$200".
  const markdownContent = useMemo(
    () => protectCurrencyFromMath(cleanContent),
    [cleanContent],
  );
  const linkPreview = useMemo(() => selectLinkPreview(message), [message]);
  const searchImages = useMemo(() => selectSearchImages(message), [message]);
  // A search image is a third-party URL nobody has fetched yet, so some of
  // them will not load: hotlink blocks, 404s, a URL that was never an image.
  // Without this the tile kept its border and showed the alt text as a wall
  // of prose, which reads as a broken page rather than as one fewer picture.
  const [failedImages, setFailedImages] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const visibleImages = searchImages.filter((image) => !failedImages.has(image.url));

  // Build a ref→source lookup once per render. Memoized so the rehype plugin
  // identity stays stable until the source list actually changes.
  const sourcesMap = useMemo(() => {
    const m = new Map<number, NonNullable<ChatMessage['researchSources']>[number]>();
    for (const s of message.researchSources ?? []) {
      if (typeof s.ref === 'number') m.set(s.ref, s);
    }
    return m;
  }, [message.researchSources]);

  const rehypePlugins = useMemo(() => {
    const base: any[] = [[rehypeHighlight, { detect: true }], rehypeKatex];
    if (sourcesMap.size > 0) base.push([rehypeCitations, { sources: sourcesMap }]);
    return base;
  }, [sourcesMap]);

  if (isUser) {
    return (
      <div className="flex justify-end mb-4">
        <div
          className="max-w-[85%] px-4 py-2.5 text-sm leading-relaxed"
          style={{
            background: 'var(--color-user-bubble)',
            color: 'var(--color-user-bubble-text)',
            borderRadius: 'var(--radius-xl) var(--radius-xl) var(--radius-sm) var(--radius-xl)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {attachedDocuments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {attachedDocuments.map((doc) => (
                <span
                  key={doc.name}
                  className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px]"
                  style={{
                    background: 'var(--color-bg-tertiary)',
                    color: 'var(--color-text-secondary)',
                  }}
                  title={`${doc.name} — ${doc.text.length.toLocaleString()} characters sent with this turn`}
                >
                  <Paperclip size={10} aria-hidden="true" />
                  {doc.name}
                </span>
              ))}
            </div>
          )}
          {sessionImages && sessionImages.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2">
              {sessionImages.map((src, index) => (
                <img
                  key={index}
                  src={src}
                  alt="Attached"
                  className="max-h-40 rounded-lg"
                  style={{ border: '1px solid var(--color-input-border)' }}
                />
              ))}
            </div>
          )}
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="group mb-6">
      {/* Deep Research timeline (steps + status) */}
      {(message.isResearch || (message.researchTraces && message.researchTraces.length > 0)) && (
        <ResearchTimeline
          traces={message.researchTraces ?? []}
          isLive={isLive}
          hasContent={cleanContent.length > 0}
        />
      )}

      {/* Tool calls are recorded on the message but deliberately not shown:
          they exist so the next turn's history can prove the assistant acted
          through tools, which is what stops it claiming an app opened when
          it never called anything. Surfacing every open_app / spotify_control
          in the transcript is noise for someone who just wanted the app
          opened. Render ToolCallCard here to bring them back. */}

      {/* Compact playback control for generated speech. */}
      {message.audio?.url && (
        <AudioPlayer
          src={message.audio.url}
          autoPlay={message.audio.autoPlay}
        />
      )}

      {/* Assistant message */}
      {cleanContent && (
        <div className="prose max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={rehypePlugins}
            components={{
              pre: CodeBlockPre,
              a: ({ href, children, node: _node, ...props }) => (
                <a href={href} {...externalLinkAttributes(href)} {...props}>
                  {children}
                </a>
              ),
            }}
          >
            {markdownContent}
          </ReactMarkdown>
        </div>
      )}

      {linkPreview && <LinkPreviewCard preview={linkPreview} />}

      {visibleImages.length > 0 && (
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {visibleImages.map((image) => (
            <a
              key={image.url}
              href={image.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block overflow-hidden rounded-xl border"
              style={{ borderColor: 'var(--color-border)' }}
              aria-label={image.description || 'Open search image'}
            >
              <img
                src={image.url}
                alt={image.description || ''}
                loading="lazy"
                decoding="async"
                referrerPolicy="no-referrer"
                onError={() =>
                  setFailedImages((previous) => {
                    if (previous.has(image.url)) return previous;
                    const next = new Set(previous);
                    next.add(image.url);
                    return next;
                  })
                }
                className="aspect-video h-full w-full object-cover"
              />
            </a>
          ))}
        </div>
      )}

      {/* Footer: copy + x-ray */}
      <div className="flex items-center gap-2 mt-1.5">
        <CopyMessageButton content={cleanContent} />
      </div>
      <XRayFooter
        usage={message.usage}
        telemetry={message.telemetry}
        isResearch={message.isResearch}
      />
    </div>
  );
}

export const MessageBubble = memo(MessageBubbleComponent);
