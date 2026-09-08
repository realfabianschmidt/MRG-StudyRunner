"""Wire types and pure probes shared by both DataCore processes.

Nothing here imports host- or worker-side code, or anything outside
``data_core``/``shared``/the standard library -- that is what makes it safe
for both processes to depend on.
"""
