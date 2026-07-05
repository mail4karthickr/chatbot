import { Composer } from './components/Composer'
import { Header } from './components/Header'
import { MessageList } from './components/MessageList'
import './App.css'

function App() {
  return (
    <div className="app">
      <Header />
      <MessageList />
      <Composer />
    </div>
  )
}

export default App
