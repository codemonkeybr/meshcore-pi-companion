import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RepeaterLogin } from '../components/RepeaterLogin';

describe('RepeaterLogin', () => {
  const defaultProps = {
    repeaterName: 'TestRepeater',
    loading: false,
    error: null as string | null,
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
    expect(screen.getByText('Login with Password')).toBeInTheDocument();
    expect(screen.getByText('Login as Guest / ACLs')).toBeInTheDocument();
  });

  it('calls onLogin with trimmed password on submit', () => {
    render(<RepeaterLogin {...defaultProps} />);

    const input = screen.getByPlaceholderText('Repeater password...');
    fireEvent.change(input, { target: { value: '  secret  ' } });
    fireEvent.submit(screen.getByText('Login with Password').closest('form')!);

    expect(defaultProps.onLogin).toHaveBeenCalledWith('secret');
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
