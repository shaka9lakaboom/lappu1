'use client';

import { COURSE_LIMITS } from '@skillmirror/contracts';
import { useActionState, useState } from 'react';

import type { CourseFormState } from '@/app/courses/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface CourseFormProps {
  action: (state: CourseFormState, formData: FormData) => Promise<CourseFormState>;
}

export function CourseForm({ action }: CourseFormProps) {
  const [state, formAction, pending] = useActionState(action, undefined);
  // One key per form instance: a double submit or retry creates the course once.
  const [idempotencyKey] = useState(() => `course-${crypto.randomUUID()}`);

  return (
    <form action={formAction} className="space-y-4" data-testid="course-form">
      <input type="hidden" name="idempotency_key" value={idempotencyKey} />
      <div className="space-y-2">
        <Label htmlFor="name">Course name</Label>
        <Input
          id="name"
          name="name"
          required
          maxLength={COURSE_LIMITS.nameMaxChars}
          placeholder="Introduction to Python Programming"
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="subject">Subject (optional)</Label>
          <Input id="subject" name="subject" maxLength={COURSE_LIMITS.subjectMaxChars} placeholder="Computer Science" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="level">Level (optional)</Label>
          <Input id="level" name="level" maxLength={COURSE_LIMITS.levelMaxChars} placeholder="Beginner" />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="description">Description or modules (optional)</Label>
        <textarea
          id="description"
          name="description"
          rows={5}
          maxLength={COURSE_LIMITS.descriptionMaxChars}
          className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder="What the course covers, e.g. its modules or learning outcomes."
        />
      </div>
      {state?.error ? (
        <p role="alert" className="text-sm text-destructive">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" disabled={pending}>
        {pending ? 'Creating…' : 'Create course'}
      </Button>
    </form>
  );
}
