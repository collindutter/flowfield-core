import { useEffect, useState } from "react";
import { DropdownMenu } from "radix-ui";
import { Check, Monitor, Moon, Sun } from "lucide-react";
import { SidebarMenuButton } from "@/components/ui/sidebar";

type Theme = "system" | "light" | "dark";
const key = "flowfield.theme";
const options = { system: "System", light: "Light", dark: "Dark" } as const;
function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(key);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}
function applyTheme(theme: Theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}
// Apply before React mounts; preferences are presentation-only, not project state.
applyTheme(readTheme());

export function ThemeMenu() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    applyTheme(theme);
    const query = matchMedia("(prefers-color-scheme: dark)");
    const changed = () => applyTheme(theme);
    const stored = (event: StorageEvent) => {
      if (event.key === key || event.key === null) setTheme(readTheme());
    };
    query.addEventListener("change", changed);
    window.addEventListener("storage", stored);
    return () => {
      query.removeEventListener("change", changed);
      window.removeEventListener("storage", stored);
    };
  }, [theme]);
  const Icon = theme === "system" ? Monitor : theme === "dark" ? Moon : Sun;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <SidebarMenuButton
          tooltip="Appearance"
          aria-label={`Appearance: ${options[theme]}`}
        >
          <Icon />
          <span className="group-data-[collapsible=icon]:hidden">
            Appearance
          </span>
        </SidebarMenuButton>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="theme-menu"
          side="right"
          align="end"
          sideOffset={8}
          aria-label="Appearance"
        >
          <DropdownMenu.RadioGroup
            value={theme}
            onValueChange={(value) => {
              const next = value as Theme;
              setTheme(next);
              try {
                localStorage.setItem(key, next);
              } catch {
                /* Still apply for this window. */
              }
            }}
          >
            {Object.entries(options).map(([value, label]) => (
              <DropdownMenu.RadioItem
                className="theme-option"
                value={value}
                key={value}
              >
                <span className="theme-check">
                  <DropdownMenu.ItemIndicator>
                    <Check size={14} />
                  </DropdownMenu.ItemIndicator>
                </span>
                {label}
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
