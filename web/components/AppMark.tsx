type AppMarkProps = {
  className?: string;
  label?: string;
};

export function AppMark({ className = '', label = 'Email becomes done' }: AppMarkProps) {
  return (
    <div className={`app-mark ${className}`.trim()} role="img" aria-label={label}>
      <span aria-hidden="true">📨</span>
      <span className="app-mark-arrow" aria-hidden="true">
        →
      </span>
      <span aria-hidden="true">✅</span>
    </div>
  );
}
