'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import SymbolSearch from './SymbolSearch';

export function Navbar() {
  const pathname = usePathname();
  const isHome = pathname === '/';

  return (
    <header className="border-b bg-white shadow-sm">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-14 items-center justify-between">
          <div className="flex items-center gap-4">
            <Link href="/" className="flex items-center gap-2">
              <span className="text-xl font-bold text-gray-900">Quantiv</span>
              <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">
                BETA
              </span>
            </Link>
            <nav className="hidden sm:flex items-center gap-6 ml-6 text-sm">
              <Link
                href="/"
                className={isHome ? 'font-medium text-gray-900' : 'text-gray-500 hover:text-gray-900'}
              >
                Home
              </Link>
              <Link
                href="/about"
                className={pathname === '/about' ? 'font-medium text-gray-900' : 'text-gray-500 hover:text-gray-900'}
              >
                About
              </Link>
            </nav>
          </div>
          <div className="w-64">
            <SymbolSearch />
          </div>
        </div>
      </div>
    </header>
  );
}
