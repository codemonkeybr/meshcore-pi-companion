import { getContactAvatar } from '../utils/contactAvatar';

interface ContactAvatarProps {
  name: string | null;
  publicKey: string;
  size?: number;
  contactType?: number;
  clickable?: boolean;
  variant?: 'default' | 'corrupt';
}

function CorruptAvatarGraphic({ size }: { size: number }) {
  return (
    <svg
      data-testid="corrupt-avatar"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 56"
      width={Math.round(size * 0.6)}
      height={Math.round(size * 0.94)}
      shapeRendering="crispEdges"
      aria-hidden="true"
    >
      <path fill="#F8E8F8" d="M8 0v24H0v32h24V0" />
      <path
        fill="#807098"
        d="M12 0h2v1h1v1h1v1h-1v1h-2V3h-1V0zm3 0h1v1h-1V0zm2 0h2v1h-1v1h-1V0zm4 0h2v1h-2V0zm-2 1h2v1h-2V1zm4 0h1v3h-2v1h-1V3h1V2h1V1zm-6 2h1v2h-1V3zm2 0h1v2h-1V3zM9 4h4v1H9V4zm5 1h3v4h-1V6h-2V5zm4 0h1v1h-1V5zm2 0h1v1h1V5h2v1h-1v2h-1v1h-2v1h-1V9h-1V7h1V6h1V5zM8 7h1v1H8V7zm0 3h2v1h1v1H9v-1H8v-1zm13 0h1v1h1v1h-2v-2zm2 0h1v1h-1v-1zm-11 1h1v1h-1v-1zm2 0h1v1h-1v-1zm3 0h1v1h-1v-1zm2 0h1v1h-1v-1zM8 12h1v1H8v-1zm3 0h1v1h-1v-1zm2 0h1v1h-1v-1zm2 0h1v1h2v1h1v1h-2v-1h-3v-1h1v-1zm-5 1h1v1h-1v-1zm2 0h1v1h-1v-1zm7 0h2v1h-2v-1zm3 0h2v1h-2v-1zM8 15h1v1H8v-1zm11 0h2v1h1v1h-2v-1h-1v-1zm4 0h1v1h-1v-1zm-13 1h1v1h-1v-1zm2 0h1v1h-1v-1zm2 0h4v1h-4v-1zm-2 2h1v1h-1v-1zm3 0h1v1h1v1h-3v-1h1v-1zm4 0h4v1h-2v1h-3v-1h1v-1zM9 19h1v1H9v-1zm2 0h1v1h-1v-1zm10 1h3v2h-1v-1h-2v-1zm-11 1h2v2H9v-1h1v-1zm4 0h2v3h1v-1h1v2h1v1h-2v-1h-2v-1h-1v-1h-1v-1h1v-1zm6 0h1v1h-1v-1zm-8 2h1v1h-1v-1zm7 0h2v1h-1v1h-1v-2zm4 0h1v1h-1v-1zM0 24h8v3H7v2h3v1H8v1H7v-1H1v-1h5v-4H0v-1zm9 0h3v1H9v-1zm4 0h1v1h-1v-1zm8 0h1v1h-1v-1zm-1 1h1v1h-1v-1zm2 0h2v3h-1v-2h-1v-1zm-6 1h1v2h-1v-2zm3 0h1v2h-1v-2zm2 0h1v2h-1v-2zm-11 2h2v1h1v1h-2v-1h-1v-1zm7 0h2v1h-2v-1zm3 0h1v1h-1v-1zm2 0h1v1h-1v-1zm-6 1h1v2h-1v-2zm3 0h1v1h1v-1h1v2h1v1h-1v2h-1v-1h-1v1h-1v-1h-2v-1h2v-1h-1v-1h1v-1zm4 0h1v1h-1v-1zm-12 2h1v1h-1v-1zm2 0h1v1h-1v-1zm2 0h1v1h-1v-1zM5 32h1v1H5v-1zm3 1h2v1H8v-1zm3 0h1v1h-1v-1zm3 0h1v1h-1v-1zm9 0h1v1h-1v-1zM6 34h2v1H7v1H6v1h1v1H1v-1H0v-1h1v-1h1v1h1v-1h1v1h1v-1h1v-1zm11 0h2v1h-1v1h1v-1h1v1h1v1h-5v1h-1v-2h2v-2zm5 0h1v1h-1v-1zM8 35h4v1h1v2h-3v-1H7v-1h1v-1zm5 0h2v1h-2v-1zm9 1h1v1h-1v-1zm-1 1h1v1h-1v-1zM2 39h1v2H2v-2zm2 0h1v2H4v-2zm2 0h1v2H6v-2zm2 0h2v2H9v-1H8v-1zm3 0h1v1h-1v-1zm4 0h1v1h-1v-1zm7 0h1v1h-1v-1zm-5 1h1v1h-1v-1zm2 0h1v1h-1v-1zm2 0h1v1h-1v-1zM1 41h1v1H1v-1zm2 0h1v1H3v-1zm2 0h1v1H5v-1zm2 0h1v1H7v-1zm3 0h2v2h-1v-1h-1v-1zm4 0h2v1h7v1h-1v1h-1v-1h-1v3h-4v-1h1v-2h-2v-1h-1v-1zm9 0h1v1h-1v-1zm-10 2h2v1h1v1h-4v1h1v1h1v-1h1v1h1v1h-4v-1h-1v-3h2v-1zm10 0h1v1h-1v-1zM0 45h10v3H8v-2H0v-1zm22 0h1v1h-1v-1zm-1 1h1v1h-1v-1zm2 0h1v2h-1v-2zM4 48h4v2H7v-1H6v1H5v-1H4v-1zm6 0h2v3h-1v-1h-1v-2zm6 0h7v1h-7v-1zM0 49h4v1h1v1H0v-2zm13 0h1v1h-1v-1zm2 0h1v1h2v1h-3v-2zm-9 1h1v2H6v-2zm2 0h2v1h1v1h1v1H7v-1h1v-2zm11 0h5v1h-1v1h-1v-1h-1v1h-3v-1h1v-1zm-7 1h3v1h-3v-1zM0 52h6v1h1v1H5v-1H4v1h1v1h1v1H4v-1H0v-3zm12 1h1v1h-1v-1zm2 0h1v3h-2v-2h1v-1zm2 0h2v3h-1v-1h-1v-2zm6 0h2v1h-1v1h-2v-1h1v-1zM7 54h1v1H7v-1zm12 0h1v1h-1v-1zM8 55h4v1H8v-1zm15 0h1v1h-1v-1z"
      />
      <path
        fill="#181010"
        d="M8 0h1v1H8V0zm0 6h1v1H8V6zm2 0h1v1h-1V6zm2 0h1v1h-1V6zm2 0h1v1h-1V6zM8 9h1v1H8V9zm3 0h1v1h-1V9zm2 0h1v1h-1V9zm2 0h1v1h-1V9zm6 0h1v1h-1V9zm2 0h1v1h-1V9zM8 11h1v1H8v-1zm9 1h5v1h-5v-1zm6 0h1v1h-1v-1zm-13 2h4v1h-4v-1zm9 0h4v1h-4v-1zm-4 1h1v1h-1v-1zm2 0h2v1h-2v-1zm-5 2h1v1h-1v-1zm4 0h2v1h-2v-1zm3 0h2v1h-2v-1zm3 0h1v1h-1v-1zM8 18h1v2H8v-2zm1 2h1v1H9v-1zm2 0h2v1h-2v-1zm4 0h1v1h-1v-1zm1 2h4v1h-4v-1zm6 0h2v1h-2v-1zM9 25h1v1H9v-1zm2 0h5v1h-1v1h-1v-1h-1v1h1v1h-2v-1h-1v-2zM0 26h1v1H0v-1zm8 0h1v1h1v1H8v-2zm7 1h1v1h-1v-1zm-8 1h1v1H7v-1zm-7 2h1v1H0v-1zm9 0h2v1H9v-1zm5 0h2v1h-2v-1zM1 31h5v1H1v-1zm6 0h1v1H7v-1zm-7 1h1v2H0v-2zm9 0h7v1H9v-1zm14 0h1v1h-1v-1zM2 33h1v1H2v-1zm2 0h1v1H4v-1zm3 0h1v1H7v-1zm1 1h2v1H8v-1zm4 0h1v1h-1v-1zM0 35h1v1H0v-1zm23 1h1v1h-1v-1zm-1 1h1v2h-1v-2zM2 38h1v1H2v-1zm2 0h1v1H4v-1zm2 0h1v1H6v-1zm2 0h4v1H8v-1zm6 0h1v1h-1v-1zm3 1h1v1h-1v-1zm2 0h1v1h-1v-1zm2 0h1v1h-1v-1zm2 0h1v1h-1v-1zm-10 1h1v1h-1v-1zm9 0h1v1h-1v-1zM8 42h1v1H8v-1zm10 4h3v2h-2v-1h-1v-1zm-1 1h1v1h-1v-1zm-1 2h1v1h-1v-1zm7 0h1v1h-1v-1zm-5 3h3v1h-3v-1zm4 0h1v1h-1v-1z"
      />
      <path
        fill="#F0B088"
        d="M8 5h1v1H8V5zm1 1h1v1h1V6h1v1h1V6h1v1h1V6h1v2H9V6zM8 8h1v1H8V8zm1 1h2v1H9V9zm3 0h1v1h-1V9zm2 0h1v1h-1V9zm6 0h1v1h-1V9zm2 0h1v1h-1V9zm-6 3h1v1h-1v-1zm6 0h1v1h-1v-1zM9 14h1v1h4v-1h2v1h-1v1H9v-2zm14 0h1v1h-1v-1zm-7 1h1v1h-1v-1zm-8 1h1v1h3v1H8v-2zm5 1h3v1h-3v-1zm5 0h1v1h-1v-1zm3 0h1v1h-1v-1zm2 0h1v1h-1v-1zM8 20h1v1H8v-1zm2 0h1v1h-1v-1zm3 0h2v1h-2v-1zm-5 2h1v1H8v-1zm12 0h2v1h-2v-1zm-10 3h1v2h1v1h-2v-1H9v-1h1v-1zm3 1h1v1h-1v-1zm2 0h1v1h-1v-1zm-1 1h1v1h-1v-1zM0 29h1v1H0v-1zm11 1h3v1h-3v-1zm-5 1h1v1H6v-1zm-5 2h1v1H1v-1zm2 0h1v1H3v-1zm2 0h2v1H5v-1zm5 0h1v1h1v1h-2v-2zm3 1h3v1h-3v-1zM0 37h1v1H0v-1zm8 0h2v1H8v-1zm15 0h1v1h-1v-1zM1 38h1v1H1v-1zm2 0h1v1H3v-1zm2 0h1v1H5v-1zm2 0h1v1H7v-1zm5 0h2v1h-2v-1zm3 0h7v1h-1v1h-1v-1h-1v1h-1v-1h-1v1h-1v-1h-1v-1zM0 39h1v2H0v-2zm10 0h1v1h-1v-1zm-2 1h1v1H8v-1zm3 0h2v1h-2v-1zm3 0h2v1h-2v-1zm9 0h1v1h-1v-1zm-7 1h7v1h-7v-1zm7 1h1v1h-1v-1zm-3 3h1v1h-1v-1zm-4 1h2v1h-1v1h-1v-2zm2 1h1v1h-1v-1zm-1 2h6v1h-6v-1zm-1 3h2v1h-2v-1zm5 0h1v1h-1v-1zm2 0h1v1h-1v-1z"
      />
    </svg>
  );
}

export function ContactAvatar({
  name,
  publicKey,
  size = 28,
  contactType,
  clickable,
  variant = 'default',
}: ContactAvatarProps) {
  if (variant === 'corrupt') {
    return (
      <div
        className={`flex items-center justify-center rounded-md flex-shrink-0 select-none bg-black/10${clickable ? ' cursor-pointer' : ''}`}
        style={{
          width: size,
          height: size,
        }}
        aria-hidden="true"
      >
        <CorruptAvatarGraphic size={size} />
      </div>
    );
  }

  const avatar = getContactAvatar(name, publicKey, contactType);

  return (
    <div
      className={`flex items-center justify-center rounded-full font-semibold flex-shrink-0 select-none${clickable ? ' cursor-pointer' : ''}`}
      style={{
        background: avatar.background,
        color: avatar.textColor,
        width: size,
        height: size,
        fontSize: size * 0.45,
      }}
      aria-hidden="true"
    >
      {avatar.text}
    </div>
  );
}
