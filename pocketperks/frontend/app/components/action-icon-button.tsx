type Props = {
  icon: "report" | "start" | "delete";
  label: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
};

export default function ActionIconButton({ icon, label, onClick, disabled = false, danger = false }: Props) {
  return (
    <button type="button" className={`icon-action${danger ? " danger" : ""}`} aria-label={label}
      title={label} onClick={onClick} disabled={disabled}>
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        {icon === "report" && <><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></>}
        {icon === "start" && <path d="m8 5 11 7-11 7V5Z"/>}
        {icon === "delete" && <><path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="m5 7 1 13h12l1-13M9 7V4h6v3"/></>}
      </svg>
    </button>
  );
}
