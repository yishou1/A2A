import { Empty, Typography } from 'antd'

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>{title}</Typography.Title>
          <Typography.Text type="secondary">该模块将在后续开发切片中接入真实数据和操作。</Typography.Text>
        </div>
      </div>
      <div className="surface placeholder-surface">
        <Empty description="功能开发中" />
      </div>
    </section>
  )
}
