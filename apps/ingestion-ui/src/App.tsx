import { useEffect, useState } from 'react'
import { useAppDispatch } from './app/hooks'
import { fetchS3Files } from './features/s3/s3Slice'
import { fetchCatalog } from './features/catalog/catalogSlice'
import { Banners } from './components/Banners'
import { ChatCard } from './components/ChatCard'
import { Header } from './components/Header'
import { LogStream } from './components/LogStream'
import { ModalHost } from './components/modal/ModalHost'
import { Stats } from './components/Stats'
import { TabBar } from './components/TabBar'
import type { Tab } from './components/TabBar'
import { Toast } from './components/Toast'
import { TreeCard } from './components/tree/TreeCard'
import './App.css'

// The URL hash is our tab source of truth. Refresh preserves the tab,
// and the browser back button navigates between tabs naturally.
function readHash(): Tab {
  return window.location.hash === '#chat' ? 'chat' : 'documents'
}

function App() {
  const dispatch = useAppDispatch()
  const [tab, setTab] = useState<Tab>(readHash)

  useEffect(() => {
    dispatch(fetchS3Files())
    dispatch(fetchCatalog())
  }, [dispatch])

  useEffect(() => {
    const onHashChange = () => setTab(readHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  function selectTab(t: Tab) {
    window.location.hash = t === 'documents' ? '' : 'chat'
    setTab(t)
  }

  return (
    <div className="app">
      <TabBar current={tab} onSelect={selectTab} />
      {tab === 'documents' ? (
        <>
          <Header />
          <Stats />
          <Banners />
          <TreeCard />
          <LogStream />
        </>
      ) : (
        <ChatCard />
      )}
      <Toast />
      <ModalHost />
    </div>
  )
}

export default App
