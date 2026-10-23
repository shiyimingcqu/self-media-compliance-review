import { theme as antTheme, type ThemeConfig } from 'antd';
export function createTheme(): ThemeConfig {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string) => css.getPropertyValue(name).trim();
  return {
    algorithm: antTheme.defaultAlgorithm,
    token: {
      colorPrimary: token('--accent'), colorInfo: token('--state-advisory'), colorSuccess: token('--state-pass'),
      colorWarning: token('--state-uncertain'), colorError: token('--state-fail'),
      colorBgBase: token('--bg-base'), colorBgLayout: token('--bg-base'), colorBgContainer: token('--bg-surface'), colorBgElevated: token('--bg-elevated'),
      colorText: token('--text-primary'), colorTextSecondary: token('--text-secondary'), colorTextTertiary: token('--text-tertiary'), colorTextQuaternary: token('--text-disabled'),
      colorTextDisabled: token('--text-disabled'), colorTextLightSolid: token('--text-primary'),
      colorBorder: token('--border-default'), colorBorderSecondary: token('--border-subtle'),
      colorFillSecondary: token('--hover-surface'), colorFillTertiary: token('--bg-inset'),
      borderRadius: 8, borderRadiusLG: 14, borderRadiusSM: 6, controlHeight: 36, controlHeightLG: 42, controlHeightSM: 28,
      fontFamily: token('--font-ui'), fontSize: 13, fontSizeHeading1: 24, fontSizeHeading2: 20, fontSizeHeading3: 16, lineHeight: 1.45,
      boxShadow: '0 8px 32px rgba(30,41,59,0.06)', boxShadowSecondary: '0 12px 40px rgba(30,41,59,0.10)', motionDurationSlow: '0.2s', motionDurationMid: '0.18s', motionDurationFast: '0.15s',
    },
    components: {
      Table: { headerBg: token('--bg-inset'), headerColor: token('--text-tertiary'), cellPaddingBlock: 10, cellPaddingInline: 12, rowHoverBg: token('--hover-surface') },
      Card: { paddingLG: 16, headerHeight: 40 }, Button: { primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none' },
      Menu: { itemHeight: 32, itemMarginBlock: 4, itemBorderRadius: 6, itemSelectedBg: token('--accent-muted'), itemSelectedColor: token('--accent'), itemBg: 'transparent' },
      Input: { activeShadow: 'none', colorBgContainer: token('--bg-inset') }, Select: { optionSelectedBg: token('--accent-muted') },
      Modal: { contentBg: token('--bg-elevated'), headerBg: token('--bg-elevated') }, Drawer: { colorBgElevated: token('--bg-elevated') },
    },
  };
}
