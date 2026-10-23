import { Link } from 'react-router-dom';
import { Card, Space, Tag } from 'antd';
import { FileTextOutlined, FormOutlined, VideoCameraOutlined, SafetyCertificateOutlined, ArrowRightOutlined } from '@ant-design/icons';
import './workflow.css';

export function WorkflowOverview({ projectId }: { projectId: string }) {
  return <Card className="workflow-overview" title="品牌内容审核工作流" extra={<Link to={`/projects/${projectId}/briefs`}>查看审核参考资料 →</Link>}>
    <div className="workflow-nodes">
      {[
        { icon: <FileTextOutlined />, title: '配置项目统一规则', text: '脚本与视频规则 · 全部达人共用', tag: '品牌配置一次' },
        { icon: <FormOutlined />, title: '达人提交与 AI 验收', text: '上传对应脚本 · 必须项通过后送审', tag: '达人端操作' },
        { icon: <VideoCameraOutlined />, title: '完整视频审查', text: 'Brief + 已通过脚本 · 工具取证', tag: 'AI 分析' },
        { icon: <SafetyCertificateOutlined />, title: '人工最终复核', text: '核对原片与证据 · 通过或退回', tag: '品牌决定' },
      ].map((node, i) => <div className="workflow-node-wrap" key={node.title}><div className="workflow-node"><span className="workflow-node-icon">{node.icon}</span><strong>{node.title}</strong><span>{node.text}</span><Tag>{node.tag}</Tag></div>{i < 3 && <ArrowRightOutlined className="workflow-arrow" />}</div>)}
    </div>
    <Space wrap className="workflow-caption"><Tag color="blue">固定流程 · 第一版</Tag><span>脚本通过后开始视频审核；新脚本或规则变化后重新确认，历史记录保留。</span></Space>
  </Card>;
}
