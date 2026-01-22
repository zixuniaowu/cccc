import { useMemo, useEffect, useRef } from 'react';
import MarkdownIt from 'markdown-it';
import { classNames } from '../utils/classNames';

interface MarkdownRendererProps {
    content: string;
    isDark: boolean;
    className?: string;
}

export function MarkdownRenderer({ content, isDark, className }: MarkdownRendererProps) {
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
                return (
                    '<div class="code-block-wrapper relative group">' +
                    '<div class="code-block-header flex items-center justify-between">' +
                    '<span class="uppercase">' + finalLang + '</span>' +
                    '<button class="copy-button flex items-center gap-1 select-none" data-code="' + encodeURIComponent(str) + '">' +
                    '<svg class="w-3.5 h-3.5 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2"></path></svg>' +
                    '<span class="pointer-events-none">Copy</span>' +
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

                // 简单的反馈效果
                const originalContent = button.innerHTML;
                button.innerHTML = '<span class="text-green-500 dark:text-emerald-400 flex items-center gap-1"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>Copied!</span>';
                button.classList.add('pointer-events-none');

                setTimeout(() => {
                    button.innerHTML = originalContent;
                    button.classList.remove('pointer-events-none');
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
                'markdown-body prose max-w-none prose-sm sm:prose-base',
                isDark ? 'prose-invert' : '',
                '[&_p]:m-0 [&_ul]:my-1 [&_ol]:my-1',
                '[&_a]:!text-current [&_a]:underline',
                className
            )}
            style={{ color: 'inherit' }}
            dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
    );
}
