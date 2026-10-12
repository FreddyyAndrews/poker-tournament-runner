import { useEffect, useState } from 'react'
import { Home } from './pages/Home'
import { Launch } from './pages/Launch'
import { MatchView } from './pages/MatchView'
import { RunView } from './pages/RunView'

// Hash routes: #/  #/match/<key>  #/run/<id>  (keys and ids may contain '/').
type Route = { page: 'home' } | { page: 'new' } | { page: 'match'; key: string; seat?: string; as?: string }
  | { page: 'run'; id: string }

function parse(hash: string): Route {
  const [rawPath, query = ''] = hash.replace(/^#\/?/, '').split('?')
  const path = decodeURIComponent(rawPath)
  const q = new URLSearchParams(query)
  if (path.startsWith('match/')) return { page: 'match', key: path.slice(6), seat: q.get('seat') ?? undefined,
                                          as: q.get('as') ?? undefined }
  if (path.startsWith('run/')) return { page: 'run', id: path.slice(4) }
  if (path === 'new') return { page: 'new' }
  return { page: 'home' }
}

export function App() {
  const [route, setRoute] = useState(() => parse(location.hash))
  useEffect(() => {
    const on = () => setRoute(parse(location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  return (
    <div className="shell">
      <header className="topbar">
        <a href="#/" className="brand">Poker Runner</a>
        <nav>
          <a href="#/new" className={route.page === 'new' ? 'on' : ''} data-testid="nav-new">New</a>
          <a href="#/" className={route.page === 'home' ? 'on' : ''}>Recent</a>
        </nav>
      </header>
      <main className="page">
        {route.page === 'home' && <Home />}
        {route.page === 'new' && <Launch />}
        {route.page === 'match' && <MatchView key={route.key + (route.seat ?? '')} matchKey={route.key}
                                              seatToken={route.seat} playAs={route.as} />}
        {route.page === 'run' && <RunView key={route.id} runId={route.id} />}
      </main>
    </div>
  )
}
