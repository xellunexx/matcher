import type { SemanticChoice, SemanticLink } from './api';

export function SemanticLinkReview({ links, choices, onChange, disabled, previouslyConfirmed = [] }: {
  links: SemanticLink[];
  choices: SemanticChoice[];
  onChange: (choices: SemanticChoice[]) => void;
  disabled: boolean;
  previouslyConfirmed?: string[];
}) {
  if (!links.length) return null;
  function choose(id: string, verdict: SemanticChoice['verdict']) {
    const remaining = choices.filter((choice) => choice.link_id !== id);
    onChange(choices.some((choice) => choice.link_id === id && choice.verdict === verdict)
      ? remaining : [...remaining, { link_id: id, verdict }]);
  }
  return <fieldset disabled={disabled} className="mt-2 space-y-1 border-t border-border pt-2">
    <legend className="text-xs font-medium text-content-primary">Кое съответствие е вярно?</legend>
    <p className="text-xs text-content-tertiary">
      Натисни двойката думи за „същото“ или „Различно“. Запомнят се само избраните връзки при решението,
      не всички думи и не цената. Повторно натискане изчиства избора.
    </p>
    {links.map((link) => {
      const verdict = choices.find((choice) => choice.link_id === link.id)?.verdict;
      return <div key={link.id} className="flex flex-wrap items-center gap-1 text-xs">
        <span className="text-content-secondary">{link.label}:</span>
        {previouslyConfirmed.includes(link.id) && <span className="text-semantic-success">
          Човек: потвърдено преди
        </span>}
        <button type="button" aria-pressed={verdict === 'same'}
          aria-label={`${link.label}: ${link.query} ↔ ${link.candidate} — същото`}
          onClick={() => choose(link.id, 'same')}
          className={`rounded border px-2 py-1 disabled:opacity-50 ${verdict === 'same'
            ? 'border-semantic-success bg-semantic-success-bg' : 'border-border bg-surface-primary'}`}>
          {link.query} ↔ {link.candidate}{verdict === 'same' ? ' — същото' : ''}
        </button>
        <button type="button" aria-pressed={verdict === 'different'}
          aria-label={`${link.label}: различно`} onClick={() => choose(link.id, 'different')}
          className={`rounded border px-2 py-1 disabled:opacity-50 ${verdict === 'different'
            ? 'border-semantic-error bg-semantic-error-bg' : 'border-border'}`}>Различно</button>
      </div>;
    })}
    {choices.some((choice) => choice.verdict === 'different') && <p role="status" className="text-xs text-content-secondary">
      Отбелязано е различие. Избери друга оферта или „Nothing here fits“, вместо да потвърждаваш тази цена.
    </p>}
  </fieldset>;
}
