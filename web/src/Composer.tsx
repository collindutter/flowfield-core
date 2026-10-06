import {
  useEffect,
  useLayoutEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";
import { Command } from "cmdk";
import { Popover } from "radix-ui";
import { Paperclip, FileText, X, Slash } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { request } from "./workspace";
import type { components } from "./api-schema";

type FileRef = { name: string; href: string };
// Canonical Markdown links survive every existing message/answer/feedback path.
// Bytes belong to the service; drafts and history carry only immutable references.
function split(value: string) {
  const files: FileRef[] = [];
  const text = value.replace(
    /\n\n\[([^\n]*?)\]\((\/api\/projects\/[\w-]+\/attachments\/[a-f0-9]{32})\)/g,
    (_, name: string, href: string) => {
      files.push({ name, href });
      return "";
    },
  );
  return { text, files };
}
function combine(text: string, files: FileRef[]) {
  return (
    text + files.map((file) => `\n\n[${file.name}](${file.href})`).join("")
  );
}

export function Composer({
  projectId,
  taskId,
  value,
  onChange,
  label,
  placeholder,
  disabled = false,
  maxLength,
  controls,
  action,
  onSend,
  onBusy,
  onModel,
  onStop,
  nativeCommands,
  inputRef,
  collapsed = false,
  draftKey,
  active = true,
}: {
  projectId: string;
  taskId?: string;
  value: string;
  onChange: (value: string) => void;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  maxLength: number;
  controls?: ReactNode;
  action?: ReactNode;
  onSend?: () => void;
  onBusy: (busy: boolean) => void;
  onModel?: () => void;
  onStop?: () => void;
  nativeCommands?: {
    items: components["schemas"]["AgentCommand"][];
    loading: boolean;
    error: string;
    load: (refresh?: boolean) => void;
  };
  inputRef?: Ref<HTMLTextAreaElement>;
  collapsed?: boolean;
  draftKey?: string;
  active?: boolean;
}) {
  const { text, files } = split(value);
  const latest = useRef(value);
  const currentDraft = useRef(draftKey);
  useLayoutEffect(() => {
    latest.current = value;
    currentDraft.current = draftKey;
  }, [value, draftKey]);
  const textarea = useRef<HTMLTextAreaElement | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  useImperativeHandle(inputRef, () => textarea.current!);
  const alive = useRef(true);
  const pending = useRef(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const menu =
    active &&
    !collapsed &&
    focused &&
    !dismissed &&
    /^\/(?:[a-z]*|codex [a-z0-9_.$-]*)$/i.test(text);
  const nativeLoad = nativeCommands?.load;
  useEffect(() => {
    if (menu) nativeLoad?.();
  }, [menu, nativeLoad]);
  const nativeQuery = text.startsWith("/codex") ? text.slice(7) : text.slice(1);
  const agentCommands = (nativeCommands?.items ?? []).filter((item) =>
    item.name.startsWith(nativeQuery),
  );
  const actions = [
    {
      name: "attach",
      detail: "Add a file or image",
    },
    ...(onModel
      ? [{ name: "model", detail: "Choose model, effort and access" }]
      : []),
    ...(onStop ? [{ name: "stop", detail: "Stop the current turn" }] : []),
  ].filter((item) => item.name.startsWith(text.slice(1).toLowerCase()));
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      onBusy(false);
    };
  }, [onBusy]);
  useLayoutEffect(() => {
    const node = textarea.current;
    if (node) {
      node.style.height = "auto";
      node.style.height = `${Math.min(node.scrollHeight, 180)}px`;
    }
  }, [text, collapsed]);
  function change(next: string) {
    setDismissed(false);
    onChange(combine(next, files));
  }
  async function upload(selected: File[]) {
    if (disabled || pending.current || !selected.length) return;
    if (files.length + selected.length > 4) {
      setError("Attach at most four files.");
      return;
    }
    pending.current = true;
    setUploading(true);
    onBusy(true);
    setError("");
    try {
      for (const file of selected) {
        if (!file.size || file.size > 2 * 1024 * 1024)
          throw new Error("Files must be nonempty and at most 2 MiB.");
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 8192)
          binary += String.fromCharCode(
            ...bytes.subarray(offset, offset + 8192),
          );
        const saved = await request<components["schemas"]["Attachment"]>(
          `projects/${projectId}/attachments`,
          "POST",
          {
            name: file.name,
            mime: file.type,
            data: btoa(binary),
            task_id: taskId,
          },
        );
        if (!alive.current || currentDraft.current !== draftKey) return;
        const current = split(latest.current);
        const name = saved.name.replace(/[[\]\\]/g, "_");
        const next = combine(current.text, [
          ...current.files,
          { name, href: saved.href },
        ]);
        if (next.length > maxLength)
          throw new Error("Shorten the message before attaching more files.");
        latest.current = next;
        onChange(next);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      pending.current = false;
      if (alive.current) {
        setUploading(false);
        onBusy(false);
      }
    }
  }
  return (
    <div className="composer-wrap">
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        aria-label="Attach files"
        onChange={(event) => {
          void upload(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      {error && (
        <p role="alert" className="composer-error">
          {error}
        </p>
      )}
      <Popover.Root
        open={menu}
        onOpenChange={(open) => {
          if (!open) setDismissed(true);
        }}
      >
        <Command
          shouldFilter={false}
          vimBindings={false}
          label={label}
          className="composer-command"
        >
          <Popover.Anchor asChild>
            <div
              className={`composer-input${collapsed ? " composer-collapsed" : ""}`}
              onDragOver={(event) => {
                if (event.dataTransfer.types.includes("Files"))
                  event.preventDefault();
              }}
              onDrop={(event) => {
                if (event.dataTransfer.files.length) {
                  event.preventDefault();
                  void upload(Array.from(event.dataTransfer.files));
                }
              }}
            >
              {!collapsed && !!files.length && (
                <div className="composer-files">
                  {files.map((file) => (
                    <span key={file.href} className="composer-file">
                      <FileText size={14} />
                      <a href={file.href} download title={file.name}>
                        {file.name}
                      </a>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        disabled={disabled}
                        aria-label={`Remove ${file.name}`}
                        onClick={() =>
                          onChange(
                            combine(
                              text,
                              files.filter((item) => item.href !== file.href),
                            ),
                          )
                        }
                      >
                        <X size={12} />
                      </Button>
                    </span>
                  ))}
                </div>
              )}
              {!collapsed && (
                <Command.Input asChild value={text} onValueChange={change}>
                  <Textarea
                    ref={textarea}
                    aria-label={label}
                    role={menu ? "combobox" : "textbox"}
                    aria-expanded={menu}
                    placeholder={placeholder ?? `${label}…`}
                    rows={2}
                    disabled={disabled}
                    maxLength={maxLength - (value.length - text.length)}
                    onFocus={() => setFocused(true)}
                    onBlur={() => setFocused(false)}
                    onPaste={(event) => {
                      if (event.clipboardData.files.length) {
                        event.preventDefault();
                        void upload(Array.from(event.clipboardData.files));
                      }
                    }}
                    onKeyDown={(event) => {
                      if (
                        !menu &&
                        [
                          "ArrowUp",
                          "ArrowDown",
                          "Home",
                          "End",
                          "Enter",
                        ].includes(event.key)
                      )
                        event.stopPropagation();
                      if (event.nativeEvent.isComposing) {
                        event.stopPropagation();
                        return;
                      }
                      if (menu && event.key === "Escape") {
                        event.preventDefault();
                        event.stopPropagation();
                        setDismissed(true);
                      } else if (
                        event.key === "Enter" &&
                        (!menu ||
                          event.metaKey ||
                          event.ctrlKey ||
                          event.shiftKey)
                      ) {
                        event.preventDefault();
                        event.stopPropagation();
                        if (event.metaKey || event.ctrlKey || event.shiftKey) {
                          const input = event.currentTarget;
                          const start = input.selectionStart;
                          const next = `${text.slice(0, start)}\n${text.slice(input.selectionEnd)}`;
                          if (
                            next.length <=
                            maxLength - (value.length - text.length)
                          ) {
                            change(next);
                            requestAnimationFrame(() =>
                              input.setSelectionRange(start + 1, start + 1),
                            );
                          }
                        } else if (!uploading) onSend?.();
                      }
                    }}
                  />
                </Command.Input>
              )}
              <div className="composer-toolbar">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Add attachment"
                  hidden={collapsed}
                  title="Attach text, code or image"
                  disabled={disabled || uploading || files.length >= 4}
                  onClick={() => picker.current?.click()}
                >
                  <Paperclip size={16} />
                </Button>
                {controls}
                <span className="composer-spacer" />
                {uploading && (
                  <span role="status" className="detail-metadata">
                    Uploading…
                  </span>
                )}
                {action}
              </div>
            </div>
          </Popover.Anchor>
          <Popover.Portal>
            <Popover.Content
              side="top"
              align="start"
              sideOffset={8}
              className="composer-command-menu"
              onOpenAutoFocus={(event) => event.preventDefault()}
              onCloseAutoFocus={(event) => event.preventDefault()}
              onFocusOutside={(event) => event.preventDefault()}
            >
              <Command.List aria-label="Composer commands">
                {!actions.length && !nativeCommands && (
                  <p className="detail-metadata">
                    No composer command. Esc to keep writing.
                  </p>
                )}
                {!!actions.length && (
                  <Command.Group heading="Flowfield">
                    {actions.map((item) => (
                      <Command.Item
                        key={item.name}
                        value={`flowfield:${item.name}`}
                        onSelect={() => {
                          change("");
                          setDismissed(true);
                          if (item.name === "attach") picker.current?.click();
                          else if (item.name === "model") onModel?.();
                          else onStop?.();
                        }}
                        onMouseDown={(event) => event.preventDefault()}
                      >
                        <Slash size={14} />
                        <span>/{item.name}</span>
                        <span className="detail-metadata">{item.detail}</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}
                {nativeCommands && (
                  <Command.Group heading="Codex">
                    {nativeCommands.loading ? (
                      <p className="detail-metadata" role="status">
                        Loading commands…
                      </p>
                    ) : nativeCommands.error ? (
                      <p className="detail-metadata" role="status">
                        {nativeCommands.error}
                      </p>
                    ) : !agentCommands.length ? (
                      <p className="detail-metadata">
                        No matching Codex commands.
                      </p>
                    ) : null}
                    {!nativeCommands.loading &&
                      !nativeCommands.error &&
                      agentCommands.map((item) => (
                        <Command.Item
                          key={item.name}
                          value={`codex:${item.name}`}
                          disabled={!!item.unavailable_reason || disabled}
                          onMouseDown={(event) => event.preventDefault()}
                          onSelect={() => {
                            change(`/codex ${item.name} `);
                            setDismissed(true);
                          }}
                        >
                          <Slash size={14} />
                          <span>/codex {item.name}</span>
                          <span className="detail-metadata">
                            {item.unavailable_reason ?? item.description}
                            {!item.unavailable_reason && item.input_hint
                              ? ` · ${item.input_hint}`
                              : ""}
                          </span>
                        </Command.Item>
                      ))}
                    <Command.Item
                      value="reload-codex-commands"
                      disabled={nativeCommands.loading}
                      onMouseDown={(event) => event.preventDefault()}
                      onSelect={() => nativeCommands.load(true)}
                    >
                      Reload Codex commands
                    </Command.Item>
                  </Command.Group>
                )}
              </Command.List>
            </Popover.Content>
          </Popover.Portal>
        </Command>
      </Popover.Root>
    </div>
  );
}
