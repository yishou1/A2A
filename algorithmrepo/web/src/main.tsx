import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider, theme as antTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { App } from './app/App'
import './styles.css'

const routerBasename =
  import.meta.env.BASE_URL === '/'
    ? undefined
    : import.meta.env.BASE_URL.replace(/\/$/, '')

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: false },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: antTheme.darkAlgorithm,
        token: {
          colorPrimary: '#4aa3c7',
          colorInfo: '#4aa3c7',
          colorSuccess: '#4db68b',
          colorWarning: '#d6a34d',
          colorError: '#d46a62',
          colorBgBase: '#071018',
          colorBgContainer: '#0b151e',
          colorBgElevated: '#0f1b25',
          colorBorder: '#243441',
          colorSplit: '#243441',
          colorText: '#d6e0e7',
          colorTextSecondary: '#95a6b2',
          borderRadius: 5,
          controlHeight: 32,
          fontSize: 13,
          boxShadowSecondary: '0 10px 30px rgb(0 0 0 / 35%)',
          fontFamily:
            "Inter, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Table: {
            cellPaddingBlockSM: 9,
            cellPaddingInlineSM: 11,
            headerBg: '#0f1b25',
            headerColor: '#95a6b2',
            rowHoverBg: '#10212c',
            borderColor: '#243441',
          },
          Layout: { bodyBg: '#071018', headerBg: '#09131c', siderBg: '#09131b' },
          Menu: {
            darkItemBg: '#09131b',
            darkItemColor: '#81939f',
            darkItemHoverBg: '#0e1c26',
            darkItemSelectedBg: '#173a49',
            darkItemSelectedColor: '#d8f2fb',
            itemBorderRadius: 4,
          },
        },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter basename={routerBasename}>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
