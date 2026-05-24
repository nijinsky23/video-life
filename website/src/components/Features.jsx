const features = [
  {
    icon: '🎨',
    title: '9 GLSL Synthesis Engines',
    desc: 'Lissajous, Plasma, Ramp Colorizer, Feedback, Kaleidoscope, Waveform 3D, Circuit Bent, Harmonic Web, and Video FX — all running live on your GPU.',
  },
  {
    icon: '🎵',
    title: 'Audio Reactive',
    desc: 'Live microphone or line input drives visuals in real time. Bass, mid, and treble bands independently modulate any parameter.',
  },
  {
    icon: '🎹',
    title: 'MIDI & Ableton Link',
    desc: 'Full MIDI CC learn on every knob. Ableton Link keeps your beat phase locked to Live, Traktor, GarageBand, and any Link-enabled app on the network.',
  },
  {
    icon: '📷',
    title: 'Live Camera Input',
    desc: 'Feed any camera — built-in, iPhone Continuity Camera, USB webcam, or RTSP stream — directly into the synthesis pipeline.',
  },
  {
    icon: '⏺️',
    title: 'Record to MP4',
    desc: 'One click captures your visuals at full quality direct to MP4. Built-in clip editor with trim and export.',
  },
  {
    icon: '🖥️',
    title: 'Dual Screen Output',
    desc: 'Main window shows controls; a second fullscreen output window goes to your projector or second display. Press F to toggle fullscreen.',
  },
]

export default function Features() {
  return (
    <section className="w-full max-w-5xl px-6 py-16">
      <h2 className="text-3xl font-bold text-white text-center mb-3 tracking-tight">Everything you need</h2>
      <p className="text-gray-400 text-center mb-12">A complete video synthesizer in one app.</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
        {features.map(f => (
          <div key={f.title}
            className="rounded-2xl p-6 flex flex-col gap-3 transition-all"
            style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}
            onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(168,85,247,0.4)'}
            onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'}
          >
            <span className="text-3xl">{f.icon}</span>
            <h3 className="text-white font-semibold text-base">{f.title}</h3>
            <p className="text-gray-400 text-sm leading-relaxed">{f.desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
