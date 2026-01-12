import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'CCCC',
  description: 'Multi-Agent Collaboration Kernel',

  // GitHub Pages base path
  base: '/cccc/',

  // Ignore dead links in legacy vnext docs
  ignoreDeadLinks: [
    /archive/
  ],

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/cccc/logo.svg' }]
  ],

  themeConfig: {
    logo: '/logo.svg',

    nav: [
      { text: 'Guide', link: '/guide/' },
      { text: 'Reference', link: '/reference/architecture' }
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'User Guide',
          items: [
            { text: 'Introduction', link: '/guide/' }
          ]
        },
        {
          text: 'Getting Started',
          collapsed: false,
          items: [
            { text: 'Overview', link: '/guide/getting-started/' },
            { text: 'Web UI Quick Start', link: '/guide/getting-started/web' },
            { text: 'CLI Quick Start', link: '/guide/getting-started/cli' }
          ]
        },
        {
          text: 'Core Guides',
          items: [
            { text: 'Workflows', link: '/guide/workflows' },
            { text: 'Web UI', link: '/guide/web-ui' },
            { text: 'Best Practices', link: '/guide/best-practices' },
            { text: 'FAQ', link: '/guide/faq' }
          ]
        },
        {
          text: 'IM Bridge',
          collapsed: false,
          items: [
            { text: 'Overview', link: '/guide/im-bridge/' },
            { text: 'Telegram', link: '/guide/im-bridge/telegram' },
            { text: 'Slack', link: '/guide/im-bridge/slack' },
            { text: 'Discord', link: '/guide/im-bridge/discord' },
            { text: 'Feishu', link: '/guide/im-bridge/feishu' },
            { text: 'DingTalk', link: '/guide/im-bridge/dingtalk' }
          ]
        }
      ],
      '/reference/': [
        {
          text: 'Reference',
          items: [
            { text: 'Architecture', link: '/reference/architecture' },
            { text: 'Features', link: '/reference/features' },
            { text: 'CLI', link: '/reference/cli' }
          ]
        }
      ]
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/ChesterRa/cccc' }
    ],

    footer: {
      message: 'Released under the Apache-2.0 License.',
      copyright: 'Copyright 2024-present CCCC Contributors'
    },

    search: {
      provider: 'local'
    }
  }
})
