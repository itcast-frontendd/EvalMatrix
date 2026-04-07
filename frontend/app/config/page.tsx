'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function ConfigRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace('/'); }, [router]);
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: '#64748b' }}>
      Redirecting to main app...
    </div>
  );
}
