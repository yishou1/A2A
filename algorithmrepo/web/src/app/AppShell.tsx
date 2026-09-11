import {
  ApartmentOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  ExportOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  NodeIndexOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Layout, Menu, Space, Tooltip, Typography } from 'antd'
import { useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { queryKeys, useHealth } from '../api/queries'

const { Header, Sider, Content } = Layout
const amosConsoleUrl =
  (import.meta.env.VITE_AMOS_URL as string | undefined)?.trim() ||
  (import.meta.env.DEV ? 'http://127.0.0.1:5000/' : '/')

export function AppShell() {
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const health = useHealth()

  const selectedKey = useMemo(() => {
    if (location.pathname.startsWith('/algorithms')) return '/algorithms'
    if (location.pathname.startsWith('/deployments')) return '/deployments'
    if (location.pathname.startsWith('/invoke')) return '/invoke'
    if (location.pathname.startsWith('/functions')) return '/functions'
    if (location.pathname.startsWith('/traces')) return '/traces'
    return '/overview'
  }, [location.pathname])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.health })
    void queryClient.invalidateQueries({ queryKey: queryKeys.algorithms })
  }

  return (
    <Layout className="app-layout">
      <Sider width={228} collapsed={collapsed} trigger={null} className="app-sider">
        <div className="brand">
          <div className="brand-mark">A</div>
          {!collapsed && (
            <div>
              <div className="brand-title">AlgoLib</div>
              <div className="brand-subtitle">算法管理台</div>
            </div>
          )}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          onClick={({ key }) => navigate(key)}
          items={[
            { key: '/overview', icon: <DashboardOutlined />, label: '运行总览' },
            { key: '/algorithms', icon: <AppstoreOutlined />, label: '算法目录' },
            { key: '/deployments', icon: <CloudServerOutlined />, label: '部署管理' },
            { key: '/invoke', icon: <DeploymentUnitOutlined />, label: '在线调用' },
            { key: '/functions', icon: <ApartmentOutlined />, label: '功能点矩阵' },
            { key: '/traces', icon: <NodeIndexOutlined />, label: '执行链路' },
          ]}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Space size="small">
            <Button
              type="text"
              aria-label={collapsed ? '展开导航' : '收起导航'}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
            />
            <Button
              href={amosConsoleUrl}
              target="_blank"
              rel="noopener noreferrer"
              icon={<ExportOutlined />}
            >
              AMOS 任务台
            </Button>
          </Space>
          <Space size="middle">
            <Tooltip title={health.error ? '服务不可达' : '服务连接正常'}>
              <Badge
                status={health.error ? 'error' : health.isFetching ? 'processing' : 'success'}
                text={health.error ? '服务断开' : '服务正常'}
              />
            </Tooltip>
            <Typography.Text type="secondary" className="header-cache">
              Runner {health.data?.runner_cache_size ?? '—'}
            </Typography.Text>
            <Button icon={<ReloadOutlined />} onClick={refresh} loading={health.isFetching}>
              刷新
            </Button>
          </Space>
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
