import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import { Toaster } from './components/ui/toast'
import './index.css'
import { normalizeProtocolLaunch, registerPwa } from './lib/notification-client'

// Register before mounting React so both the initial route and subsequent
// hash changes use the existing authenticated notification-click flow.
normalizeProtocolLaunch()
window.addEventListener('hashchange',normalizeProtocolLaunch)
registerPwa()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><Toaster><App /></Toaster></React.StrictMode>
)
