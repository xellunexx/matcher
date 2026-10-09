// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
import { forwardRef, type ButtonHTMLAttributes, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: React.ReactNode;
  iconPosition?: 'left' | 'right';
  /**
   * In-app route. Renders an anchor instead of a button: a plain left
   * click still takes the SPA path (same speed as `navigate()`), while
   * Ctrl/⌘/Shift/middle-click and the right-click menu get the browser's
   * native "open in new tab/window" behaviour.
   */
  to?: string;
}

const variantStyles: Record<ButtonVariant, string> = {
  primary: clsx(
    'bg-oe-blue text-content-inverse',
    'hover:bg-oe-blue-hover active:bg-oe-blue-active',
    'shadow-xs hover:shadow-md',
    'border border-transparent',
    'hover:scale-[1.02] active:scale-[0.98]',
  ),
  secondary: clsx(
    'bg-surface-primary text-content-primary',
    'border border-border',
    'hover:bg-surface-secondary active:bg-surface-tertiary',
    'shadow-xs hover:shadow-sm',
    'active:scale-[0.98]',
  ),
  ghost: clsx(
    'bg-transparent text-content-secondary',
    'hover:bg-surface-secondary active:bg-surface-tertiary',
    'border border-transparent',
    'active:scale-[0.98]',
  ),
  danger: clsx(
    'bg-semantic-error text-content-inverse',
    'hover:opacity-90 active:opacity-80',
    'shadow-xs hover:shadow-md',
    'border border-transparent',
    'hover:scale-[1.02] active:scale-[0.98]',
  ),
};

const sizeStyles: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5 rounded-md',
  md: 'h-8 px-3.5 text-sm gap-1.5 rounded-lg',
  lg: 'h-10 px-5 text-sm gap-2 rounded-xl',
};

function buttonClasses(
  variant: ButtonVariant,
  size: ButtonSize,
  isDisabled: boolean,
  className?: string,
) {
  return clsx(
    'inline-flex items-center justify-center',
    'font-medium whitespace-nowrap select-none',
    'transition-all duration-normal ease-oe transform-gpu will-change-transform',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-oe-blue focus-visible:ring-offset-2',
    variantStyles[variant],
    sizeStyles[size],
    isDisabled && 'opacity-40 pointer-events-none',
    className,
  );
}

function buttonContent(
  loading: boolean,
  size: ButtonSize,
  icon: React.ReactNode,
  iconPosition: 'left' | 'right',
  children: React.ReactNode,
) {
  if (loading) return <Spinner size={size} />;
  return (
    <>
      {icon && iconPosition === 'left' && <span className="shrink-0">{icon}</span>}
      {children && <span className="inline-flex items-center">{children}</span>}
      {icon && iconPosition === 'right' && <span className="shrink-0">{icon}</span>}
    </>
  );
}

/** Anchor variant for `to` - kept separate so Button stays hook-free for
 *  callers/tests outside a Router. Plain click goes through the SPA router;
 *  modified and middle clicks are left to the browser (new tab/window). */
const AnchorButton = forwardRef<HTMLAnchorElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      loading = false,
      icon,
      iconPosition = 'left',
      disabled,
      className,
      children,
      to,
      onClick,
      type: _buttonOnlyType,
      ...props
    },
    ref,
  ) => {
    const navigate = useNavigate();
    const isDisabled = disabled || loading;
    const handleClick = (e: MouseEvent<HTMLAnchorElement>) => {
      onClick?.(e as unknown as MouseEvent<HTMLButtonElement>);
      if (
        e.defaultPrevented ||
        e.button !== 0 ||
        e.ctrlKey ||
        e.metaKey ||
        e.shiftKey ||
        e.altKey
      ) {
        return;
      }
      e.preventDefault();
      if (!isDisabled && to) navigate(to);
    };
    return (
      <a
        ref={ref}
        href={isDisabled ? undefined : to}
        aria-disabled={isDisabled || undefined}
        role="button"
        className={buttonClasses(variant, size, isDisabled, className)}
        onClick={handleClick}
        {...(props as ButtonHTMLAttributes<HTMLAnchorElement>)}
      >
        {buttonContent(loading, size, icon, iconPosition, children)}
      </a>
    );
  },
);
AnchorButton.displayName = 'AnchorButton';

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ to, ...props }, ref) => {
    if (to !== undefined) {
      return (
        <AnchorButton
          to={to}
          ref={ref as React.Ref<HTMLAnchorElement>}
          {...props}
        />
      );
    }
    const {
      variant = 'primary',
      size = 'md',
      loading = false,
      icon,
      iconPosition = 'left',
      disabled,
      className,
      children,
      ...rest
    } = props;
    const isDisabled = disabled || loading;

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        className={buttonClasses(variant, size, isDisabled, className)}
        {...rest}
      >
        {buttonContent(loading, size, icon, iconPosition, children)}
      </button>
    );
  },
);

Button.displayName = 'Button';
export { Button };
export type { ButtonProps };

/* ── Spinner ──────────────────────────────────────────────────────────── */

function Spinner({ size = 'md' }: { size?: ButtonSize }) {
  const sizeMap = { sm: 'h-3.5 w-3.5', md: 'h-4 w-4', lg: 'h-5 w-5' };
  return (
    <svg
      className={clsx('animate-spin', sizeMap[size])}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}
