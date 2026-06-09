import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "rtia-theme";

/** Read the theme the no-flash inline script in index.html already applied. */
function currentTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/**
 * Light/dark theme toggle. The initial class is set before React mounts by
 * the inline script in index.html (avoids a white flash), so this hook only
 * reconciles React state with the DOM and persists the user's explicit choice.
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Private-mode / disabled storage — theme still applies for the session.
    }
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  };
}
