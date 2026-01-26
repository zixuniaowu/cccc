import { useMemo, useEffect, useRef } from 'react';
import MarkdownIt from 'markdown-it';
import { classNames } from '../utils/classNames';

interface MarkdownRendererProps {
    content: string;
    isDark: boolean;
    className?: string;
    /** Force light text (for colored backgrounds like user messages) */
    invertText?: boolean;
}

export function MarkdownRenderer({ content, isDark, className, invertText }: MarkdownRendererProps) {
    const containerRef = useRef<HTMLDivElement>(null);

    const md = useMemo(() => {
        const instance = new MarkdownIt({
            html: false, // Security: Disable raw HTML to prevent XSS
            linkify: true,
            typographer: true,
            breaks: true,
            highlight: (str: string, lang: string): string => {
                const finalLang = lang?.toLowerCase().trim() || 'code';
                const escaped = instance.utils.escapeHtml(str);

                // 代码块带 Copy 按钮，无语法高亮
                // 使用 CSS 类切换显示状态，避免直接修改 innerHTML 与 React reconciliation 冲突
                return (
                    '<div class="code-block-wrapper relative group">' +
                    '<div class="code-block-header flex items-center justify-between">' +
                    '<span class="uppercase">' + finalLang + '</span>' +
                    '<button class="copy-button flex items-center gap-1 select-none" data-code="' + encodeURIComponent(str) + '">' +
                    '<span class="copy-icon pointer-events-none"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2"></path></svg></span>' +
                    '<span class="copy-text pointer-events-none">Copy</span>' +
                    '<span class="copied-icon pointer-events-none hidden text-green-500 dark:text-emerald-400"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg></span>' +
                    '<span class="copied-text pointer-events-none hidden text-green-500 dark:text-emerald-400">Copied!</span>' +
                    '</button>' +
                    '</div>' +
                    '<pre><code class="language-' + finalLang + '">' + escaped + '</code></pre>' +
                    '</div>'
                );
            },
        });
        return instance;
    }, []);

    const htmlContent = useMemo(() => {
        return md.render(content || "");
    }, [md, content]);

    // 使用事件委托处理复制逻辑
    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        const handleCopy = async (e: MouseEvent) => {
            const button = (e.target as HTMLElement).closest('.copy-button');
            if (!button) return;

            e.preventDefault();
            e.stopPropagation();

            const code = decodeURIComponent(button.getAttribute('data-code') || '');
            if (!code) {
                console.error('No code found in data-code attribute');
                return;
            }
            try {
                // 使用 Clipboard API，如果不可用则使用 execCommand 回退
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(code);
                } else {
                    // Fallback for environments without clipboard API (PWA/HTTP)
                    const textArea = document.createElement('textarea');
                    textArea.value = code;
                    textArea.style.position = 'fixed';
                    textArea.style.left = '-9999px';
                    textArea.style.top = '0';
                    document.body.appendChild(textArea);
                    textArea.focus();
                    textArea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textArea);
                }
                console.log('Copied code:', code.substring(0, 50) + '...');

                // 使用 CSS 类切换显示状态，避免修改 innerHTML 导致 React DOM 同步错误
                button.classList.add('copied', 'pointer-events-none');
                const copyIcon = button.querySelector('.copy-icon');
                const copyText = button.querySelector('.copy-text');
                const copiedIcon = button.querySelector('.copied-icon');
                const copiedText = button.querySelector('.copied-text');
                if (copyIcon) copyIcon.classList.add('hidden');
                if (copyText) copyText.classList.add('hidden');
                if (copiedIcon) copiedIcon.classList.remove('hidden');
                if (copiedText) copiedText.classList.remove('hidden');

                setTimeout(() => {
                    button.classList.remove('copied', 'pointer-events-none');
                    if (copyIcon) copyIcon.classList.remove('hidden');
                    if (copyText) copyText.classList.remove('hidden');
                    if (copiedIcon) copiedIcon.classList.add('hidden');
                    if (copiedText) copiedText.classList.add('hidden');
                }, 2000);
            } catch (err) {
                console.error('Failed to copy code:', err);
            }
        };

        container.addEventListener('click', handleCopy);
        return () => container.removeEventListener('click', handleCopy);
    }, [htmlContent]);

    return (
        <div
            ref={containerRef}
            className={classNames(
                'markdown-body prose max-w-none prose-sm',
                (isDark || invertText) ? 'prose-invert' : '',
                '[&_p]:m-0 [&_ul]:my-1 [&_ol]:my-1',
                '[&_a]:![color:inherit] [&_a]:underline',
                className
            )}
            style={{ color: 'inherit' }}
            dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
    );
}
