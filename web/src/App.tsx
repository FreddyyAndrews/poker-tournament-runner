import { useEffect, useState } from 'react'
import { Home } from './pages/Home'
import { MatchView } from './pages/MatchView'
import { RunView } from './pages/RunView'

// Hash routes: #/  #/match/<key>  #/run/<id>  (keys and ids may contain '/').
type Route = { page: 'home' } | { page: 'match'; key: string } | { page: 'run'; id: string }

function parse(hash: string): Route {
  const path = decodeURIComponent(hash.replace(/^#\/?/, ''))
  if (path.startsWith('match/')) return { page: 'match', key: path.slice(6) }
  if (path.startsWith('run/')) return { page: 'run', id: path.slice(4) }
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
          <a href="#/" className={route.page === 'home' ? 'on' : ''}>Recent</a>
        </nav>
      </header>
      <main className="page">
        {route.page === 'home' && <Home />}
        {route.page === 'match' && <MatchView key={route.key} matchKey={route.key} />}
        {route.page === 'run' && <RunView key={route.id} runId={route.id} />}
      </main>
    </div>
  )
}
