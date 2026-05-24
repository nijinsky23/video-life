const platforms = [
  {
    key: 'mac',
    label: 'macOS',
    icon: '🍎',
    note: 'macOS 12+  •  Apple Silicon & Intel',
    suffix: 'mac.zip',
    instruction: 'After downloading: right-click → Open (first launch only, bypasses Gatekeeper)',
  },
  {
    key: 'windows',
    label: 'Windows',
    icon: '🪟',
    note: 'Windows 10/11  •  64-bit',
    suffix: 'windows.zip',
    instruction: 'Extract the zip and run Video Life.exe',
  },
  {
    key: 'linux',
    label: 'Linux',
    icon: '🐧',
    note: 'Ubuntu 22.04+  •  x86_64',
    suffix: 'linux.tar.gz',
    instruction: 'Extract and run ./Video Life  •  Requires OpenGL 3.3+',
  },
]

function getAssetUrl(release, suffix) {
  if (!release?.assets) return null
  const asset = release.assets.find(a => a.name.toLowerCase().includes(suffix))
  return asset?.browser_download_url ?? null
}

export default function Download({ release, loading }) {
  const version = release?.tag_name ?? null

  return (
    <section id="download" className="w-full max-w-4xl px-6 py-16">
      <h2 className="text-3xl font-bold text-white text-center mb-3 tracking-tight">Download</h2>
      <p className="text-gray-400 text-center mb-2">
        {loading
          ? 'Checking for latest release…'
          : version
            ? <>Latest release: <span className="text-purple-400 font-mono">{version}</span></>
            : 'Free download — no account required'}
      </p>
      <p className="text-gray-500 text-sm text-center mb-10">
        Requires a GPU with OpenGL 3.3+ (anything made after ~2012).
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
        {platforms.map(p => {
          const url = getAssetUrl(release, p.suffix)
          return (
            <div key={p.key}
              className="rounded-2xl p-6 flex flex-col gap-4"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <div className="flex items-center gap-3">
                <span className="text-3xl">{p.icon}</span>
                <div>
                  <div className="text-white font-semibold">{p.label}</div>
                  <div className="text-xs text-gray-500">{p.note}</div>
                </div>
              </div>

              {url ? (
                <a
                  href={url}
                  className="block w-full text-center py-2.5 rounded-xl text-sm font-semibold text-white transition-all"
                  style={{ background: 'linear-gradient(135deg, #7c3aed, #6366f1)' }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '0.85'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '1'}
                >
                  Download {version}
                </a>
              ) : (
                <span
                  className="block w-full text-center py-2.5 rounded-xl text-sm font-semibold"
                  style={{ background: 'rgba(255,255,255,0.06)', color: '#6b7280', cursor: loading ? 'wait' : 'default' }}>
                  {loading ? 'Loading…' : 'Coming soon'}
                </span>
              )}

              <p className="text-xs text-gray-500 leading-relaxed">{p.instruction}</p>
            </div>
          )
        })}
      </div>

      {/* Fallback: link to releases page */}
      {!loading && (
        <p className="text-center text-sm text-gray-500 mt-8">
          All releases and changelogs on{' '}
          <a
            href={`https://github.com/YOUR_USERNAME/video-life/releases`}
            target="_blank" rel="noopener noreferrer"
            className="text-purple-400 hover:text-purple-300 underline underline-offset-2">
            GitHub Releases
          </a>
        </p>
      )}
    </section>
  )
}
