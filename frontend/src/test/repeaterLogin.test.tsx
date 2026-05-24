import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RepeaterLogin } from '../components/RepeaterLogin';

describe('RepeaterLogin', () => {
  const defaultProps = {
    repeaterName: 'TestRepeater',
    loading: false,
    error: null as string | null,
    password: '',
    onPasswordChange: vi.fn(),
    rememberPassword: false,
    onRememberPasswordChange: vi.fn(),
    onLogin: vi.fn(),
    onLoginAsGuest: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders repeater name and description', () => {
    render(<RepeaterLogin {...defaultProps} />);

    expect(screen.getByText('TestRepeater')).toBeInTheDocument();
    expect(screen.getByText('Log in to access repeater dashboard')).toBeInTheDocument();
  });

  it('renders password input and buttons', () => {
    render(<RepeaterLogin {...defaultProps} />);

    expect(screen.getByPlaceholderText('Repeater password...')).toBeInTheDocument();
    expect(screen.getByText('Remember password')).toBeInTheDocument();
    expect(screen.getByText('Login with Password')).toBeInTheDocument();
    expect(screen.getByText('Login as Guest / ACLs')).toBeInTheDocument();
  });

  it('calls onLogin with trimmed password on submit', () => {
    render(<RepeaterLogin {...defaultProps} password="  secret  " />);
    fireEvent.submit(screen.getByText('Login with Password').closest('form')!);

    expect(defaultProps.onLogin).toHaveBeenCalledWith('secret');
  });

  it('propagates password changes', () => {
    render(<RepeaterLogin {...defaultProps} />);

    const input = screen.getByPlaceholderText('Repeater password...');
    fireEvent.change(input, { target: { value: 'new secret' } });

    expect(defaultProps.onPasswordChange).toHaveBeenCalledWith('new secret');
  });

  it('toggles remember password checkbox', () => {
    render(<RepeaterLogin {...defaultProps} />);

    fireEvent.click(screen.getByLabelText('Remember password'));

    expect(defaultProps.onRememberPasswordChange).toHaveBeenCalledWith(true);
  });

  it('shows storage warning when remember password is enabled', () => {
    render(<RepeaterLogin {...defaultProps} rememberPassword={true} />);

    expect(
      screen.getByText(
        /Passwords are stored unencrypted in local browser storage for this domain\./
      )
    ).toBeInTheDocument();
  });

  it('calls onLoginAsGuest when guest button clicked', () => {
    render(<RepeaterLogin {...defaultProps} />);

    fireEvent.click(screen.getByText('Login as Guest / ACLs'));
    expect(defaultProps.onLoginAsGuest).toHaveBeenCalledTimes(1);
  });

  it('disables inputs when loading', () => {
    render(<RepeaterLogin {...defaultProps} loading={true} />);

    expect(screen.getByPlaceholderText('Repeater password...')).toBeDisabled();
    expect(screen.getByText('Logging in...')).toBeDisabled();
    expect(screen.getByText('Login as Guest / ACLs')).toBeDisabled();
  });

  it('shows loading text on submit button', () => {
    render(<RepeaterLogin {...defaultProps} loading={true} />);

    expect(screen.getByText('Logging in...')).toBeInTheDocument();
    expect(screen.queryByText('Login with Password')).not.toBeInTheDocument();
  });

  it('displays error message when present', () => {
    render(<RepeaterLogin {...defaultProps} error="Invalid password" />);

    expect(screen.getByText('Invalid password')).toBeInTheDocument();
  });

  it('does not call onLogin when loading', () => {
    render(<RepeaterLogin {...defaultProps} loading={true} />);

    fireEvent.submit(screen.getByText('Logging in...').closest('form')!);
    expect(defaultProps.onLogin).not.toHaveBeenCalled();
  });
});
