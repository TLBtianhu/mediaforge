window.__ModuleLoader__.load({
  id: '@local/modelscope-panel',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    const SITE = 'https://www.modelscope.cn';
    const LINKS = [
      { label: '模型市场', href: SITE + '/models' },
      { label: '数据集', href: SITE + '/datasets' },
      { label: '我的空间', href: SITE + '/my/myhome' },
    ];

    // 侧边栏图标：跟着主题色的圆环 + 轨道点
    function Mark({ size }) {
      return h('svg', {
        viewBox: '0 0 64 64',
        width: size,
        height: size,
        'aria-hidden': true,
        style: { display: 'block' },
      },
        h('circle', { cx: 32, cy: 32, r: 26, fill: 'none', stroke: 'currentColor', strokeWidth: 4, opacity: 0.45 }),
        h('circle', { cx: 32, cy: 32, r: 8, fill: 'currentColor' }),
        h('g', { fill: 'currentColor', opacity: 0.85 },
          h('circle', { cx: 32, cy: 8, r: 3.4 }),
          h('circle', { cx: 32, cy: 56, r: 3.4 }),
          h('circle', { cx: 8, cy: 32, r: 3.4 }),
          h('circle', { cx: 56, cy: 32, r: 3.4 }),
        ),
      );
    }

    function openSite(href) {
      window.open(href, '_blank', 'noopener,noreferrer');
    }

    function Panel() {
      const card = {
        maxWidth: 620,
        border: '1px solid var(--dsw-alias-border-l1)',
        background: 'var(--dsw-alias-bg-layer-1)',
        borderRadius: 12,
        padding: '20px 22px',
      };
      const button = {
        border: 'none',
        borderRadius: 8,
        padding: '9px 16px',
        cursor: 'pointer',
        font: 'inherit',
        color: '#fff',
        background: 'var(--dsw-alias-brand-primary)',
      };
      const link = {
        color: 'var(--dsw-alias-brand-primary)',
        textDecoration: 'none',
        cursor: 'pointer',
        font: 'inherit',
        background: 'none',
        border: 'none',
        padding: 0,
      };
      return h('div', { style: { padding: '24px 28px', color: 'var(--dsw-alias-label-primary)' } },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 } },
          h('span', { style: { color: 'var(--dsw-alias-brand-primary)' } }, h(Mark, { size: 28 })),
          h('h2', { style: { margin: 0, fontSize: 18, fontWeight: 600 } }, 'ModelScope'),
        ),
        h('p', { style: { margin: '0 0 14px', color: 'var(--dsw-alias-label-secondary)', fontSize: 13, lineHeight: 1.7 } },
          '模型与数据集的国内站点。站点设置了 X-Frame-Options: SAMEORIGIN，禁止被第三方页面嵌入，',
          '所以这里放的是直达入口——点按钮会在新窗口打开。',
        ),
        h('div', { style: card },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 14, marginBottom: 14 } },
            h('img', {
              src: SITE + '/favicon.ico',
              alt: '',
              width: 32,
              height: 32,
              style: { borderRadius: 6, background: 'var(--dsw-alias-bg-layer-2)' },
            }),
            h('div', null,
              h('div', { style: { fontWeight: 600 } }, 'modelscope.cn'),
              h('div', { style: { color: 'var(--dsw-alias-label-secondary)', fontSize: 12 } }, '魔搭社区 · 模型 / 数据集 / 创空间'),
            ),
          ),
          h('div', { style: { display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 } },
            h('button', { style: button, type: 'button', onClick: () => openSite(SITE) }, '打开 modelscope.cn'),
            h('button', {
              style: { ...button, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)' },
              type: 'button',
              onClick: () => openSite(SITE + '/datasets'),
            }, '我要建数据集（发版镜像）'),
          ),
          h('div', { style: { display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13 } },
            ...LINKS.map(item => h('button', {
              key: item.href,
              type: 'button',
              style: link,
              onClick: () => openSite(item.href),
            }, item.label)),
          ),
        ),
        h('p', { style: { margin: '16px 0 0', color: 'var(--dsw-alias-label-secondary)', fontSize: 12, lineHeight: 1.7 } },
          '用来做什么：本项目的更新镜像可以托管在 ModelScope 数据集里（见 update_sources.rs 的 MODELSCOPE_*）；',
          '模型权重也可以从这里取。默认构建不启用第三方镜像，需要自己在数据集上传发布包后再打开开关。',
        ),
      );
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: 'modelscope',
          order: 20,
          label: 'ModelScope',
        }, Mark));
        ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main',
          key: 'modelscope',
        }, Panel));
      },
    };
  },
});
