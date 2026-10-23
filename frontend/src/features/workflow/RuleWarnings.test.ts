import { describe, expect, it } from 'vitest';
import { splitWarnings } from './RuleWarnings';

describe('splitWarnings', () => {
  it('把带前缀的系统提示与模型疑点分开', () => {
    const grouped = splitWarnings([
      '第20页同时出现前15s与软植前30s两项要求，建议品牌核对',
      '【系统提示】第5条引文跨了同一页的多个原文块，已按该页连续原文合并定位',
      '【系统提示】本次仅重新校验已保存的模型输出，未调用AI',
    ]);
    expect(grouped.content).toHaveLength(1);
    expect(grouped.system).toEqual([
      '第5条引文跨了同一页的多个原文块，已按该页连续原文合并定位',
      '本次仅重新校验已保存的模型输出，未调用AI',
    ]);
  });

  it('历史记录没有前缀时按措辞兜底识别系统提示', () => {
    const grouped = splitWarnings([
      '第6条引用已定位至同页完整原文块，请核对判定口径。',
      'Brief仍有2项未完整识读，需核对原件。',
      '多处示例页注明创意仅供参考，不作为硬性要求。',
      '模型返回了视频分层字段（layer/组件/启用状态），脚本与图文规则不使用三层配置，已忽略。',
    ]);
    expect(grouped.system).toHaveLength(3);
    expect(grouped.content).toEqual(['多处示例页注明创意仅供参考，不作为硬性要求。']);
  });

  it('忽略空值与空白', () => {
    expect(splitWarnings(['', '   ', undefined as unknown as string])).toEqual({ content: [], system: [] });
  });
});