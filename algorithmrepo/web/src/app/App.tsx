import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'
import { AppShell } from './AppShell'

const OverviewPage = lazy(() => import('../features/overview/OverviewPage').then((module) => ({ default: module.OverviewPage })))
const AlgorithmListPage = lazy(() => import('../features/algorithms/AlgorithmListPage').then((module) => ({ default: module.AlgorithmListPage })))
const AlgorithmDetailPage = lazy(() => import('../features/algorithms/AlgorithmDetailPage').then((module) => ({ default: module.AlgorithmDetailPage })))
const DeploymentListPage = lazy(() => import('../features/deployments/DeploymentListPage').then((module) => ({ default: module.DeploymentListPage })))
const InvocationPage = lazy(() => import('../features/invocation/InvocationPage').then((module) => ({ default: module.InvocationPage })))
const FunctionMatrixPage = lazy(() => import('../features/functions/FunctionMatrixPage').then((module) => ({ default: module.FunctionMatrixPage })))
const TracePage = lazy(() => import('../features/traces/TracePage').then((module) => ({ default: module.TracePage })))

export function App() {
  return (
    <Suspense fallback={<div className="route-loading"><Spin size="large" /></div>}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="algorithms" element={<AlgorithmListPage />} />
          <Route path="algorithms/:algorithmId/:version/:backendType" element={<AlgorithmDetailPage />} />
          <Route path="deployments" element={<DeploymentListPage />} />
          <Route path="invoke" element={<InvocationPage />} />
          <Route path="functions" element={<FunctionMatrixPage />} />
          <Route path="traces" element={<TracePage />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
