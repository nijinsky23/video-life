// TODO: replace KOFI_USERNAME with your actual Ko-fi username
const KOFI_USERNAME = 'YOUR_KOFI_USERNAME'

export default function Donate() {
  return (
    <section id="donate" className="w-full max-w-2xl px-6 py-16 text-center">
      <div className="rounded-3xl p-10 flex flex-col items-center gap-5"
        style={{
          background: 'linear-gradient(135deg, rgba(124,58,237,0.12), rgba(99,102,241,0.08))',
          border: '1px solid rgba(124,58,237,0.25)'
        }}>
        <span className="text-5xl">☕</span>
        <h2 className="text-2xl font-bold text-white tracking-tight">Support Video Life</h2>
        <p className="text-gray-400 leading-relaxed max-w-md">
          Video Life is free and open source. If it sparks something in your performances,
          installations, or experiments, a coffee keeps the synthesizer running.
        </p>
        <a
          href={`https://ko-fi.com/${KOFI_USERNAME}`}
          target="_blank"
          rel="noopener noreferrer"
          className="px-8 py-3 rounded-xl font-semibold text-white text-sm flex items-center gap-2 transition-all"
          style={{ background: 'linear-gradient(135deg, #ff5f5f, #ff8a00)' }}
          onMouseEnter={e => e.currentTarget.style.opacity = '0.88'}
          onMouseLeave={e => e.currentTarget.style.opacity = '1'}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M23.881 8.948c-.773-4.085-4.859-4.593-4.859-4.593H.723c-.604 0-.679.798-.679.798s-.082 7.324-.022 11.822c.164 2.424 2.586 2.672 2.586 2.672s8.267-.023 11.966-.049c2.438-.426 2.683-2.566 2.658-3.734 4.352.24 7.422-2.831 6.649-6.916zm-11.062 3.511c-1.246 1.453-4.011 3.976-4.011 3.976s-.121.119-.31.023c-.076-.057-.108-.09-.108-.09-.443-.441-3.368-3.049-4.034-3.954-.709-.965-1.041-2.7-.091-3.71.951-1.01 3.005-1.086 4.363.407 0 0 1.565-1.782 3.468-.963 1.904.82 1.832 2.694.723 4.311zm6.173.478c-.928.116-1.682.028-1.682.028V7.284h1.77s1.971.551 1.971 2.638c0 1.913-.985 2.667-2.059 3.015z"/>
          </svg>
          Buy me a coffee
        </a>
        <p className="text-xs text-gray-600">
          100% optional — Video Life will always be free.
        </p>
      </div>
    </section>
  )
}
