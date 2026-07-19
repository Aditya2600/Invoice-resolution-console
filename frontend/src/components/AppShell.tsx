import { Menu } from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useUiStore } from "@/store/ui";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/process", label: "Process", end: false },
  { to: "/settings", label: "Policy", end: false },
] as const;

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-3">
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
        <rect x="2" y="4" width="24" height="6" rx="1.5" fill="currentColor" />
        <rect x="2" y="12" width="18" height="4" rx="1" fill="currentColor" />
        <rect x="2" y="18" width="24" height="6" rx="1.5" fill="currentColor" />
      </svg>
      <div className="flex flex-col leading-none">
        <span className="text-[15px] font-semibold tracking-tight">Invoice Resolution</span>
        <span className="mono-label text-muted-foreground mt-1">CONSOLE</span>
      </div>
    </Link>
  );
}

function SiteHeader() {
  const open = useUiStore((state) => state.mobileNavOpen);
  const setOpen = useUiStore((state) => state.setMobileNavOpen);

  return (
    <header className="sticky top-0 z-40 bg-background/85 backdrop-blur border-b border-divider">
      <div className="mx-auto max-w-7xl px-5 md:px-8 h-16 flex items-center justify-between gap-6">
        <Logo />
        <nav className="hidden md:flex items-center gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "px-4 h-9 inline-flex items-center rounded-full text-sm transition-colors",
                  isActive
                    ? "bg-foreground text-background"
                    : "text-foreground/70 hover:text-foreground hover:bg-panel",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="hidden md:flex items-center gap-2">
          <Button asChild className="rounded-full bg-foreground text-background hover:bg-foreground/90">
            <Link to="/process">Process invoices</Link>
          </Button>
        </div>
        <div className="md:hidden">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Open menu">
                <Menu className="size-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-[280px] bg-background">
              <SheetHeader>
                <SheetTitle className="text-left">Invoice Resolution</SheetTitle>
              </SheetHeader>
              <nav className="mt-6 flex flex-col gap-1 px-4">
                {NAV.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={() => setOpen(false)}
                    className="px-3 py-2 rounded-md text-[15px] hover:bg-panel"
                  >
                    {item.label}
                  </NavLink>
                ))}
                <Button
                  asChild
                  className="mt-4 rounded-full bg-foreground text-background"
                  onClick={() => setOpen(false)}
                >
                  <Link to="/process">Process invoices</Link>
                </Button>
              </nav>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
}

export function AppShell() {
  const location = useLocation();

  return (
    <div className="min-h-screen flex flex-col">
      <SiteHeader />
      <main key={location.pathname} className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t border-divider mt-16">
        <div className="mx-auto max-w-7xl px-5 md:px-8 py-8 flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div className="mono-label text-muted-foreground">INVOICE RESOLUTION CONSOLE</div>
        </div>
      </footer>
    </div>
  );
}
