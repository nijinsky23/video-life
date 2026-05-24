import { useEffect, useState } from 'react'
import Hero from './components/Hero'
import Features from './components/Features'
import Download from './components/Download'
import Donate from './components/Donate'
import Footer from './components/Footer'
import './index.css'

// TODO: update to your GitHub username/repo after pushing
export const GITHUB_REPO = 'YOUR_USERNAME/video-life'

export default function App() {
  const [release, setRelease] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.assets) setRelease(data) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="flex flex-col items-center w-full">
      <Hero />
      <Features />
      <Download release={release} loading={loading} />
      <Donate />
      <Footer repo={GITHUB_REPO} />
    </div>
  )
}
