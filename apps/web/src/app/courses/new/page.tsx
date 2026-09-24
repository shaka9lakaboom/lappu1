import Link from 'next/link';

import { createCourse } from '@/app/courses/actions';
import { CourseForm } from '@/components/course-form';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { requireApiSession } from '@/lib/session';

export default async function NewCoursePage() {
  await requireApiSession('/courses/new');
  return (
    <main className="mx-auto max-w-2xl space-y-6 px-6 py-12">
      <Link href="/courses" className="text-sm underline underline-offset-4">
        Back to courses
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>New course</CardTitle>
          <CardDescription>
            SkillMirror generates about 30–60 assessable skills for the course and links them to the shared skill
            registry. This runs in the background and takes a moment.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <CourseForm action={createCourse} />
        </CardContent>
      </Card>
    </main>
  );
}
