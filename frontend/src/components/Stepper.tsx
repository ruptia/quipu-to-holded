interface Props {
  steps: readonly { title: string }[]
  current: number
  canOpen: (index: number) => boolean
  onSelect: (index: number) => void
}

export function Stepper({ steps, current, canOpen, onSelect }: Props) {
  return (
    <ol className="stepper">
      {steps.map((step, i) => (
        <li key={step.title} className={i === current ? 'active' : i < current ? 'done' : ''}>
          <button type="button" disabled={!canOpen(i)} onClick={() => onSelect(i)}>
            <span className="stepper-num">{i + 1}</span>
            {step.title}
          </button>
        </li>
      ))}
    </ol>
  )
}
