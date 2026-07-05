import { useEffect } from 'react'
import { useAppDispatch } from './app/hooks'
import { fetchS3Files } from './features/s3/s3Slice'
import { Banners } from './components/Banners'
import { FailedDownloads } from './components/FailedDownloads'
import { Header } from './components/Header'
import { ModalHost } from './components/modal/ModalHost'
import { Stats } from './components/Stats'
import { Toast } from './components/Toast'
import { TreeCard } from './components/tree/TreeCard'
import './App.css'

function App() {
  const dispatch = useAppDispatch()

  useEffect(() => {
    dispatch(fetchS3Files())
  }, [dispatch])

  return (
    <div className="app">
      <Header />
      <Stats />
      <Banners />
      <TreeCard />
      <FailedDownloads />
      <Toast />
      <ModalHost />
    </div>
  )
}

export default App
