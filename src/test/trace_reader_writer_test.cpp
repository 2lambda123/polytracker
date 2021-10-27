#include <catch2/catch.hpp>
#include <gigafunction/traceio/trace_reader.h>
#include <gigafunction/traceio/trace_writer.h>
#include <ctime>
#include <unistd.h>

namespace gigafunction {

TEST_CASE("Symmetry - write and read yields same result", "trace_reader_writer") {
  
  // NOTE: We are not to concerned with the potential race condition in this application
  char fname[] = "trace_reader_write_log.bin.XXXXXX";
  mktemp(fname);
  INFO("Filename is: " << fname);

  SECTION("Single in out works") {
    const thread_id tid = 2;
    const block_id bid = 123;

    // Write traces
    {
      trace_writer tw{fname};
      tw.write_trace(tid, bid);
    }

    // Read traces and verify correct
    {
      trace_reader tr{fname};
      auto e = tr.next();
      REQUIRE(e);
      REQUIRE(e.value().tid == tid);
      REQUIRE(e.value().bid == bid);
    }
  }

  SECTION("Randomized sequences") {
    srand(time(nullptr));
    auto seed = rand();
    INFO("Seed for randomized testing is " << seed << ". Use to reproduce issues.");

    std::vector<std::pair<thread_id, block_id>> reference;
    size_t gen_at_least = 1000000;
    // Write traces
    {
      trace_writer tw{fname};
      for (size_t n = 0;n<gen_at_least;) {
        thread_id tid = rand();
        uint64_t count = rand() % (gen_at_least/2); // To ensure not too many blocks for a single thread.
        for (size_t i=0;i<count;i++) {
          block_id bid = rand();
          tw.write_trace(tid, bid);
          reference.emplace_back(tid, bid);
        }
        n += count;
      }
    }
    INFO("Generated " << reference.size() << " random entries");
    // Read and verify traces
    {
      trace_reader rdr{fname};
      for (auto& e : reference) {
        auto next = rdr.next();
        REQUIRE(next);
        REQUIRE(next.value().tid == e.first);
        REQUIRE(next.value().bid == e.second);
      }

      // Empty
      REQUIRE(!rdr.next());
    }
    
  }

  unlink(fname);
}

}
