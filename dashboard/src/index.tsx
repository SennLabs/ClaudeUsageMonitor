/* @refresh reload */
import { render } from 'solid-js/web'
import { Router, Route } from '@solidjs/router'
import './index.css'
import App from './App.tsx'
import TabletDashboard from './components/TabletDashboard.tsx'
import SettingsPage from './components/SettingsPage.tsx'
import UsersPage from './components/UsersPage.tsx'

const root = document.getElementById('root')

render(
  () => (
    <Router>
      <Route path="/" component={App} />
      <Route path="/tablet" component={TabletDashboard} />
      <Route path="/settings" component={SettingsPage} />
      <Route path="/users" component={UsersPage} />
    </Router>
  ),
  root!,
)
