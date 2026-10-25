import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Identity } from './types'

const STORAGE_KEY = 'ticketpilot.identity.v1'

export const IDENTITY_PRESETS: Identity[] = [
  {
    tenant: 'demo',
    user: 'alice',
    roles: [],
    label: 'Alice · 客户',
  },
  {
    tenant: 'demo',
    user: 'bob',
    roles: [],
    label: 'Bob · 客户',
  },
  {
    tenant: 'demo',
    user: 'seat',
    roles: ['support'],
    label: 'Seat · 坐席',
  },
  {
    tenant: 'demo',
    user: 'reviewer',
    roles: ['approver', 'support'],
    label: 'Reviewer · 审批人',
  },
  {
    tenant: 'demo',
    user: 'ops-admin',
    roles: ['admin', 'approver', 'support'],
    label: 'Ops Admin · 管理员',
  },
  {
    tenant: 'acme',
    user: 'carol',
    roles: [],
    label: 'Carol · Acme 租户',
  },
]

function loadIdentity(): Identity {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return IDENTITY_PRESETS[0]
    const parsed = JSON.parse(raw) as Partial<Identity>
    if (!parsed.tenant || !parsed.user) return IDENTITY_PRESETS[0]
    const preset = IDENTITY_PRESETS.find(
      (item) => item.tenant === parsed.tenant && item.user === parsed.user,
    )
    return {
      tenant: parsed.tenant,
      user: parsed.user,
      roles: Array.isArray(parsed.roles) ? parsed.roles : [],
      label: parsed.label || preset?.label || `${parsed.user} · ${parsed.tenant}`,
    }
  } catch {
    return IDENTITY_PRESETS[0]
  }
}

interface IdentityContextValue {
  identity: Identity
  setIdentity: (identity: Identity) => void
  isApprover: boolean
  canOperate: boolean
  hasRole: (role: string) => boolean
}

const IdentityContext = createContext<IdentityContextValue | null>(null)

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentityState] = useState<Identity>(loadIdentity)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(identity))
  }, [identity])

  const value = useMemo<IdentityContextValue>(
    () => ({
      identity,
      setIdentity: setIdentityState,
      isApprover:
        identity.roles.includes('approver') ||
        identity.roles.includes('admin'),
      canOperate:
        identity.roles.includes('support') ||
        identity.roles.includes('admin'),
      hasRole: (role: string) => identity.roles.includes(role),
    }),
    [identity],
  )

  return (
    <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>
  )
}

export function useIdentity(): IdentityContextValue {
  const value = useContext(IdentityContext)
  if (!value) throw new Error('useIdentity must be used inside IdentityProvider')
  return value
}
