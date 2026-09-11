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
import { Badge, Button, Layout, Menu, Space, Tooltip } from 'antd'
import { useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { queryKeys, useHealth } from '../api/queries'

const { Header, Sider, Content } = Layout
const amosConsoleUrl =
  (import.meta.env.VITE_AMOS_URL as string | undefined)?.trim() ||
  (import.meta.env.DEV ? 'http://127.0.0.1:5000/' : '/')

const pageNames: Record<string, string> = {
  '/overview': '运行总览',
  '/algorithms': '算法目录',
  '/deployments': '部署管理',
  '/invoke': '在线调用',
  '/functions': '功能点矩阵',
  '/traces': '执行链路',
}

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
      <Header className="app-header">
        <div className="topbar-brand-group">
          <span className="brand-title">Simulation</span>
          <span className="product-name">场景仿真平台</span>
          <span className="mode-tag">算法管理</span>
        </div>
        <div className="header-page-context">
          <span>ALGORITHM OPERATIONS</span>
          <strong>{pageNames[selectedKey]}</strong>
        </div>
        <Space size="small" className="header-actions">
          <Tooltip title={health.error ? 'AlgoLib 服务不可达' : 'AlgoLib 服务连接正常'}>
            <span className={`service-chip ${health.error ? 'service-chip-error' : 'service-chip-ok'}`}>
              <Badge status={health.error ? 'error' : health.isFetching ? 'processing' : 'success'} />
              {health.error ? 'ALGOLIB · 服务断开' : 'ALGOLIB · 服务正常'}
            </span>
          </Tooltip>
          <span className="runner-cache">RUNNER {health.data?.runner_cache_size ?? '—'}</span>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={health.isFetching}>刷新</Button>
          <Button
            href={amosConsoleUrl}
            target="_blank"
            rel="noopener noreferrer"
            icon={<ExportOutlined />}
          >
            AMOS 任务台
          </Button>
        </Space>
      </Header>
      <Layout className="app-workspace">
        <Sider width={220} collapsedWidth={64} collapsed={collapsed} trigger={null} className="app-sider">
          <div className="sider-heading">
            <div className="brand-mark">A</div>
            {!collapsed && (
              <div>
                <div className="sider-title">AlgoLib</div>
                <div className="brand-subtitle">算法与功能工作区</div>
              </div>
            )}
          </div>
          <div className="sider-section-label">{collapsed ? 'OPS' : '管理视图'}</div>
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
          <div className="sider-footer">
            <Button
              type="text"
              block
              aria-label={collapsed ? '展开导航' : '收起导航'}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
            >
              {!collapsed && '收起导航'}
            </Button>
          </div>
        </Sider>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
