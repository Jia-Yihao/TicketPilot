import { useEffect, useState } from 'react'
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom'
import { checkHealth, listApprovals } from './api'
import { Icon } from './components/Icon'
import { IdentityPanel } from './components/IdentityPanel'
import { roleLabel, initials } from './format'
import { useIdentity } from './identity'
import { usePageTitle } from './hooks'
import type { Identity } from './types'
import { AgentWorkbench } from './pages/AgentWorkbench'
import { ApprovalWorkbench } from './pages/ApprovalWorkbench'
import { ChannelMatrix } from './pages/ChannelMatrix'
import { ConversationPage } from './pages/ConversationPage'
import { MemoryPage } from './pages/MemoryPage'
import { OpsDashboard } from './pages/OpsDashboard'
import { ProactiveRules } from './pages/ProactiveRules'
import { TaskDetailPage } from './pages/TaskDetailPage'
import { TaskHistoryPage } from './pages/TaskHistoryPage'
import { TraceViewer } from './pages/TraceViewer'

function ConsoleClock() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const time = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(now)

  return (
    <span className="clock" title="Asia/Shanghai">
      <span className="clock__zone mono">CST</span>
      {time}
    </span>
  )
}

type Health = 'checking' | 'online' | 'offline'

function sectionLabel(pathname: string): string {
  if (pathname.startsWith('/approvals')) return '审批工作台'
  if (pathname.startsWith('/agent')) return '坐席工作台'
  if (pathname.startsWith('/ops')) return '运营看板'
  if (pathname.startsWith('/channels')) return '渠道矩阵'
  if (pathname.startsWith('/rules')) return '通知规则'
  if (pathname.startsWith('/memory')) return '我的记忆'
  if (pathname.startsWith('/traces/')) return 'Trace 回放'
  if (pathname.startsWith('/conversation')) return '售后对话'
  if (pathname.startsWith('/tasks/')) return '任务详情'
  if (pathname.startsWith('/tasks')) return '任务档案'
  return '售后运行台'
}

function homePath(isApprover: boolean, canOperate = false): string {
  if (isApprover) return '/approvals'
  return canOperate ? '/agent' : '/conversation'
}

function AppShell() {
  const { identity, setIdentity, isApprover, canOperate } = useIdentity()
  const location = useLocation()
  const [identityOpen, setIdentityOpen] = useState(false)
  const [health, setHealth] = useState<Health>('checking')
  const [pendingCount, setPendingCount] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    async function probe() {
      const ok = await checkHealth()
      if (!cancelled) setHealth(ok ? 'online' : 'offline')
    }
    void probe()
    const timer = window.setInterval(probe, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!isApprover) {
      setPendingCount(null)
      return
    }
    let cancelled = false
    async function load() {
      try {
        const data = await listApprovals(identity, [], 1, 0)
        if (!cancelled) setPendingCount(data.counts.PENDING ?? 0)
      } catch {
        if (!cancelled) setPendingCount(null)
      }
    }
    void load()
    const timer = window.setInterval(load, 30_000)
    const onChange = () => {
      void load()
    }
    window.addEventListener('ticketpilot:approvals-changed', onChange)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.removeEventListener('ticketpilot:approvals-changed', onChange)
    }
  }, [identity, isApprover])

  function selectIdentity(next: Identity) {
    setIdentity(next)
  }

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>

      <aside className="sidebar">
        <Link
          className="brand"
          to={homePath(isApprover, canOperate)}
          aria-label="TicketPilot 首页"
        >
          <img
            className="brand__mark"
            src={`${import.meta.env.BASE_URL}favicon.svg`}
            alt=""
            width={36}
            height={36}
          />
          <span className="brand__text">
            <strong>TICKETPILOT</strong>
            <small>AFTER-SALES OPS</small>
          </span>
        </Link>

        <p className="sidebar__label">工作区</p>
        <nav className="nav" aria-label="主导航">
          <NavLink
            to="/approvals"
            className={({ isActive }) =>
              `nav-link${isActive ? ' is-active' : ''}`
            }
          >
            <span className="nav-link__icon">
              <Icon name="inbox" size={18} />
            </span>
            <span className="nav-link__label">审批工作台</span>
            {isApprover && pendingCount ? (
              <span className="nav-link__badge" aria-label={`${pendingCount} 条待审批`}>
                {pendingCount > 99 ? '99+' : pendingCount}
              </span>
            ) : null}
          </NavLink>
          {canOperate ? (
            <NavLink
              to="/agent"
              className={({ isActive }) =>
                `nav-link${isActive ? ' is-active' : ''}`
              }
            >
              <span className="nav-link__icon">
                <Icon name="user" size={18} />
              </span>
              <span className="nav-link__label">坐席工作台</span>
            </NavLink>
          ) : null}
          {canOperate ? (
            <NavLink
              to="/ops"
              className={({ isActive }) =>
                `nav-link${isActive ? ' is-active' : ''}`
              }
            >
              <span className="nav-link__icon">
                <Icon name="activity" size={18} />
              </span>
              <span className="nav-link__label">运营看板</span>
            </NavLink>
          ) : null}
          <NavLink
            to="/conversation"
            className={({ isActive }) =>
              `nav-link${isActive ? ' is-active' : ''}`
            }
          >
            <span className="nav-link__icon">
              <Icon name="chat" size={18} />
            </span>
            <span className="nav-link__label">售后对话</span>
          </NavLink>
          <NavLink
            to="/memory"
            className={({ isActive }) =>
              `nav-link${isActive ? ' is-active' : ''}`
            }
          >
            <span className="nav-link__icon">
              <Icon name="user" size={18} />
            </span>
            <span className="nav-link__label">我的记忆</span>
          </NavLink>
          {canOperate ? (
            <NavLink
              to="/channels"
              className={({ isActive }) =>
                `nav-link${isActive ? ' is-active' : ''}`
              }
            >
              <span className="nav-link__icon">
                <Icon name="activity" size={18} />
              </span>
              <span className="nav-link__label">渠道矩阵</span>
            </NavLink>
          ) : null}
          {canOperate ? (
            <NavLink
              to="/rules"
              className={({ isActive }) =>
                `nav-link${isActive ? ' is-active' : ''}`
              }
            >
              <span className="nav-link__icon">
                <Icon name="send" size={18} />
              </span>
              <span className="nav-link__label">通知规则</span>
            </NavLink>
          ) : null}
          <NavLink
            to="/tasks"
            className={({ isActive }) =>
              `nav-link${isActive ? ' is-active' : ''}`
            }
          >
            <span className="nav-link__icon">
              <Icon name="tasks" size={18} />
            </span>
            <span className="nav-link__label">任务档案</span>
          </NavLink>
        </nav>

        <div className="sidebar__env">
          <div className="sidebar__env-head">
            <span className="mono">LOCAL DEMO</span>
            <span className={`health health--${health}`}>
              <span className="health__dot" aria-hidden="true" />
              {health === 'online'
                ? 'API 已就绪'
                : health === 'offline'
                  ? 'API 不可达'
                  : '检测服务中'}
            </span>
          </div>
          <p>开发 Header 身份适配器。生产环境会拒绝该模式并要求 OIDC。</p>
        </div>
      </aside>

      <div className="shell__main">
        <header className="topbar">
          <div className="breadcrumb">
            <span className="mono">OPS</span>
            <span className="breadcrumb__sep">/</span>
            <span>售后运行台</span>
            <span className="breadcrumb__sep">/</span>
            <strong>{sectionLabel(location.pathname)}</strong>
          </div>
          <div className="topbar__right">
            <span className="policy-chip" title="任何角色（含管理员）都不能审批自己发起的申请">
              <Icon name="shield" size={13} />
              四眼复核 · 强制
            </span>
            <ConsoleClock />
            <button
              type="button"
              className="identity-chip"
              onClick={() => setIdentityOpen(true)}
              aria-haspopup="dialog"
            >
              <span className="identity-chip__avatar">
                {initials(identity.user)}
              </span>
              <span className="identity-chip__text">
                <strong>{identity.user}</strong>
                <small>
                  {identity.tenant} · {roleLabel(identity.roles)}
                </small>
              </span>
              <Icon name="chevron" size={14} />
            </button>
          </div>
        </header>

        <main className="shell__content" id="main">
          <div className="route-stage" key={`${identity.tenant}:${identity.user}`}>
            <Outlet />
          </div>
        </main>
      </div>

      <IdentityPanel
        open={identityOpen}
        current={identity}
        onSelect={selectIdentity}
        onClose={() => setIdentityOpen(false)}
      />
    </div>
  )
}

function Landing() {
  const { isApprover, canOperate } = useIdentity()
  return <Navigate to={homePath(isApprover, canOperate)} replace />
}

function MissingPage() {
  usePageTitle('页面不存在')
  const { isApprover, canOperate } = useIdentity()
  return (
    <div className="page page--centered">
      <div className="missing">
        <p className="eyebrow">404 / ROUTE NOT FOUND</p>
        <h1>这张工单不存在</h1>
        <p>请求的页面不在当前控制台范围内，可能已经归档或被移除。</p>
        <Link
          className="button button--primary"
          to={homePath(isApprover, canOperate)}
        >
          返回工作区
          <Icon name="arrow" size={16} />
        </Link>
      </div>
    </div>
  )
}

export function App() {
  const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

  return (
    <BrowserRouter basename={basename}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Landing />} />
          <Route path="approvals" element={<ApprovalWorkbench />} />
          <Route path="agent" element={<AgentWorkbench />} />
          <Route path="ops" element={<OpsDashboard />} />
          <Route path="conversation" element={<ConversationPage />} />
          <Route path="memory" element={<MemoryPage />} />
          <Route path="channels" element={<ChannelMatrix />} />
          <Route path="rules" element={<ProactiveRules />} />
          <Route path="tasks" element={<TaskHistoryPage />} />
          <Route path="tasks/:taskId" element={<TaskDetailPage />} />
          <Route path="traces/:traceId" element={<TraceViewer />} />
          <Route path="*" element={<MissingPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
