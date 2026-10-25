type IconName =
  | 'inbox'
  | 'chat'
  | 'tasks'
  | 'refresh'
  | 'search'
  | 'check'
  | 'x'
  | 'clock'
  | 'shield'
  | 'arrow'
  | 'copy'
  | 'external'
  | 'alert'
  | 'back'
  | 'lock'
  | 'user'
  | 'chevron'
  | 'send'
  | 'eye'
  | 'activity'

const PATHS: Record<IconName, React.ReactNode> = {
  inbox: (
    <>
      <path d="M4 5.5h16v13H4z" />
      <path d="M4 13h4l1.5 2h5L16 13h4" />
    </>
  ),
  chat: (
    <>
      <path d="M4 5h16v11H9l-5 4v-4H4z" />
      <path d="M8 9h8M8 12h5" />
    </>
  ),
  tasks: (
    <>
      <path d="M6 4h12v16H6z" />
      <path d="M9 8h6M9 12h6M9 16h4" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 7v5h-5" />
      <path d="M4 17v-5h5" />
      <path d="M6.1 8a7 7 0 0 1 11.4-1L20 12M4 12l2.5 5a7 7 0 0 0 11.4-1" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="5.5" />
      <path d="m15 15 5 5" />
    </>
  ),
  check: <path d="m5 12 4.5 4.5L19 7" />,
  x: <path d="m6 6 12 12M18 6 6 18" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3 5 6v5c0 4.6 2.9 8 7 10 4.1-2 7-5.4 7-10V6l-7-3Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  arrow: <path d="M4 12h15m-5-5 5 5-5 5" />,
  copy: (
    <>
      <rect x="8" y="8" width="11" height="12" />
      <path d="M16 8V4H5v12h3" />
    </>
  ),
  external: (
    <>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M18 14v6H4V6h6" />
    </>
  ),
  alert: (
    <>
      <path d="M12 3 2.8 20h18.4L12 3Z" />
      <path d="M12 9v4.5M12 17h.01" />
    </>
  ),
  back: <path d="M20 12H5m5-5-5 5 5 5" />,
  lock: (
    <>
      <rect x="5" y="10" width="14" height="10" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20c.7-3.4 3.4-5.5 7-5.5s6.3 2.1 7 5.5" />
    </>
  ),
  chevron: <path d="m8 10 4 4 4-4" />,
  send: (
    <>
      <path d="M4 12 20 4l-4 16-4-7-8-1Z" />
      <path d="m12 13 8-9" />
    </>
  ),
  eye: (
    <>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  activity: <path d="M3 12h4l2.5-7 4.5 14 2.5-7h4.5" />,
}

export function Icon({
  name,
  size = 18,
  className,
}: {
  name: IconName
  size?: number
  className?: string
}) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {PATHS[name]}
    </svg>
  )
}
