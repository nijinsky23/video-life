export default function Hero() {
  return (
    <section className="w-full flex flex-col items-center px-6 pt-20 pb-16 text-center"
      style={{ background: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(120,40,255,0.18) 0%, transparent 70%)' }}>

      {/* Logo / wordmark */}
      <div className="flex items-center gap-3 mb-6">
        {/* Simple animated SVG logo — concentric Lissajous-style rings */}
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true">
          <circle cx="24" cy="24" r="22" stroke="url(#g1)" strokeWidth="1.5" />
          <ellipse cx="24" cy="24" rx="14" ry="22" stroke="url(#g2)" strokeWidth="1.5" />
          <ellipse cx="24" cy="24" rx="22" ry="10" stroke="url(#g3)" strokeWidth="1.5" />
          <circle cx="24" cy="24" r="3" fill="#c084fc" />
          <defs>
            <linearGradient id="g1" x1="2" y1="2" x2="46" y2="46">
              <stop stopColor="#7c3aed" /><stop offset="1" stopColor="#06b6d4" />
            </linearGradient>
            <linearGradient id="g2" x1="10" y1="2" x2="38" y2="46">
              <stop stopColor="#a855f7" /><stop offset="1" stopColor="#22d3ee" />
            </linearGradient>
            <linearGradient id="g3" x1="2" y1="14" x2="46" y2="34">
              <stop stopColor="#c084fc" /><stop offset="1" stopColor="#67e8f9" />
            </linearGradient>
          </defs>
        </svg>
        <span className="text-3xl font-semibold tracking-tight text-white">Video Life</span>
      </div>

      <h1 className="text-5xl sm:text-6xl font-bold tracking-tighter text-white mb-5 max-w-2xl leading-tight">
        Real-time GPU<br />
        <span style={{ background: 'linear-gradient(90deg, #a855f7, #22d3ee)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
          video synthesizer
        </span>
      </h1>

      <p className="text-lg sm:text-xl text-gray-400 max-w-xl mb-10 leading-relaxed">
        9 GLSL synthesis engines. Audio & MIDI reactive. Ableton Link sync.
        Camera input. Record direct to MP4. Free &amp; open source.
      </p>

      <div className="flex gap-4 flex-wrap justify-center">
        <a
          href="#download"
          className="px-7 py-3 rounded-xl font-semibold text-white text-sm transition-all"
          style={{ background: 'linear-gradient(135deg, #7c3aed, #6366f1)' }}
          onMouseEnter={e => e.currentTarget.style.opacity = '0.88'}
          onMouseLeave={e => e.currentTarget.style.opacity = '1'}
        >
          Download Free
        </a>
        <a
          href="#donate"
          className="px-7 py-3 rounded-xl font-semibold text-sm border transition-all"
          style={{ borderColor: 'rgba(168,85,247,0.4)', color: '#c084fc' }}
          onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(168,85,247,0.8)'}
          onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(168,85,247,0.4)'}
        >
          Support the project ☕
        </a>
      </div>

      {/* Engines preview pills */}
      <div className="flex flex-wrap justify-center gap-2 mt-12 max-w-lg">
        {['Lissajous', 'Plasma', 'Ramp Colorizer', 'Feedback', 'Kaleidoscope',
          'Waveform 3D', 'Circuit Bent', 'Harmonic Web', 'Video FX'].map(e => (
          <span key={e}
            className="px-3 py-1 rounded-full text-xs font-medium"
            style={{ background: 'rgba(124,58,237,0.15)', border: '1px solid rgba(124,58,237,0.3)', color: '#c084fc' }}>
            {e}
          </span>
        ))}
      </div>
    </section>
  )
}
