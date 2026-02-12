import CodeMirror from '@uiw/react-codemirror';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';

interface BotCodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  height?: string;
}

export function BotCodeEditor({ value, onChange, id, height = '256px' }: BotCodeEditorProps) {
  return (
    <div className="w-full overflow-hidden rounded-md border border-input">
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={[python()]}
        theme={oneDark}
        height={height}
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: true,
        }}
        className="text-sm"
        id={id}
      />
    </div>
  );
}
